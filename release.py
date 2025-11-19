import os
import re
import sys
import logging
import shutil
import codecs
from typing import NoReturn

from .release_parser import ReleaseParser, ReleaseInfo, TargetInfo
from .gitlab_rep import GitlabRep
from .generator_tag import GeneratorTag
from .release_formatter import ReleaseFormatter
from .changelog_generator import ChangelogGenerator
from .firmware_store_pusher import FirmwareStorePusher

GITLAB_URL = 'https://gitlab.neroelectronics.by/'
JIRA_TASK_PATTERN = re.compile(r'\[([\w\-]+)\]')

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Set console encoding for proper Unicode output
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

# ---------- validation helpers ----------
_TAG_VRANGE_RE = re.compile(
    r"^v"
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # first group (project ID)
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # second group (major version)
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # third group (minor version)
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)"     # fourth group (hardware number)
    r"-Rev"
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)"     # revision number
    r"(-release)?$"                          # optional release suffix
)


def validate_target(target_name: str) -> None:
    expected_targets_str = os.getenv('EXPECTED_TARGETS', "")
    allowed = [t.strip() for t in expected_targets_str.split(",") if t.strip()]

    if not allowed:
        raise ValueError(
            f"EXPECTED_TARGETS parsed to an empty allow-list: {expected_targets_str!r}"
        )

    if target_name not in allowed:
        raise ValueError(f"Target '{target_name}' is not in allowed list: {allowed}")
    logger.info("Target validation passed: %s", target_name)


def validate_branch(info: ReleaseInfo) -> None:
    # Only release or hotfix branches are allowed (including sub-branches)
    if not re.match(r'^(release|hotfix)(/.*)?$', info.branch_name):
        raise ValueError(
            f"Invalid branch: {info.branch_name}. Allowed branches must start with 'release' or 'hotfix'."
        )
    logger.info("Branch validation passed: %s", info.branch_name)


def generate_release_email(info: ReleaseInfo, target: TargetInfo, rep: GitlabRep) -> str:
    """Generate release email text with all necessary information and links."""

    product_name = target.target_name.split('_hard')[0].upper()
    all_tasks = _collect_jira_tasks(info)

    features_text = _format_release_items(info.features)
    bugfixes_text = _format_release_items(info.bug_fixes)

    repo_url = rep.get_project_url()
    branch_type = "hotfix" if info.branch_name.startswith("hotfix") else "release"
    intro_phrase = "Для передачи заказчику" if info.upgrade_to_release else "Для передачи на тестирование"
    tag_name = target.tag_name + "-release" if info.upgrade_to_release else target.tag_name
    changelog_section = _build_changelog_section(info, repo_url)

    return f"""{intro_phrase}. {product_name} {tag_name}.

New Features:
{features_text}
Bug Fixes:
{bugfixes_text}

Прошивка передается из ветки {branch_type}.

{changelog_section}Ссылка на Tag:
{repo_url}/-/tags/{tag_name}

Ссылка на бинарный файл прошивки (*.bin):
{repo_url}/-/blob/{tag_name}/build/{target.target_name}.bin

Ссылка на файл контейнера для обновления прошивки (*.btl.bin):
{repo_url}/-/blob/{tag_name}/build/{target.container_name}

Задачи в рамках которых делалась прошивка:
{', '.join(sorted(all_tasks))}
"""

def collect_target_files(build_dir: str, targets: list[TargetInfo]) -> list[str]:
    files = []
    for t in targets:
        files.extend([
            os.path.join(build_dir, f"{t.target_name}.bin"),
            os.path.join(build_dir, f"{t.target_name}.map"),
            os.path.join(build_dir, t.container_name),
        ])
    # keep only existing files
    files = [p for p in files if os.path.exists(p)]
    return files

def validate_tag(tag_name: str, rep: GitlabRep) -> None:
    # 1) Check format and value ranges
    if not _TAG_VRANGE_RE.match(tag_name):
        raise ValueError(
            f"Invalid tag format: {tag_name}, "
            f"expected vXX.XX.XX.XX-RevXX[-release] where XX in 1..255"
        )
    # Additional validation using the common formatter
    if not ReleaseFormatter.validate_tag_string(tag_name):
        raise ValueError(f"Tag does not match canonical pattern: {tag_name}")

    # 2) Check if tag exists in GitLab
    existing_url = rep.get_tag(tag_name)   # returns URL or None
    if existing_url is not None:
        # Tag already exists, meaning release was already created
        raise RuntimeError(f"Tag '{tag_name}' already exists: {existing_url}")

    logger.info("Tag validation passed and tag doesn't exist yet: %s", tag_name)


def _collect_jira_tasks(info: ReleaseInfo) -> set[str]:
    tasks = set()
    for item in info.features + info.bug_fixes:
        match = JIRA_TASK_PATTERN.search(item)
        if match:
            tasks.add(match.group(1))
    return tasks


def _format_release_items(items: list[str]) -> str:
    return "- отсутствуют" if not items else "\n".join(f"{item}" for item in items)


def _build_changelog_section(info: ReleaseInfo, repo_url: str) -> str:
    if not info.upgrade_to_release:
        return ""
    return f"Ссылка на CHANGELOG.md:\n{repo_url}/-/blob/dev/CHANGELOG.md\n\n"


def _parse_args() -> tuple[str, str]:
    if len(sys.argv) < 3:
        print("Usage: python -m release_generator.release <path_to_release.json> <path_to_defs.h>")
        sys.exit(1)

    release_json_path = os.path.abspath(sys.argv[1])
    defs_path = os.path.abspath(sys.argv[2])
    return release_json_path, defs_path


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("Missing %s in environment variables", name)
        sys.exit(1)
    return value


def _build_single_target(target: TargetInfo, info: ReleaseInfo, token: str, build_dir: str) -> list[str]:
    temp_info = ReleaseInfo(
        git_project_id=info.git_project_id,
        branch_name=info.branch_name,
        is_service_firmware=info.is_service_firmware,
        upgrade_to_release=info.upgrade_to_release,
        features=info.features,
        bug_fixes=info.bug_fixes,
        targets=[target]
    )

    build_rep = GitlabRep(GITLAB_URL, info.git_project_id, token, temp_info, build_dir)
    build_rep.build_project()

    return [
        os.path.join(build_dir, f"{target.target_name}.bin"),
        os.path.join(build_dir, f"{target.target_name}.map"),
        os.path.join(build_dir, target.container_name),
    ]


def _ensure_artifacts_dir(build_dir: str) -> str:
    artifacts_dir = os.path.join(build_dir, 'artifacts')
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def _save_release_email(info: ReleaseInfo, target: TargetInfo, rep: GitlabRep, artifacts_dir: str, suffix: str = "") -> None:
    email_text = generate_release_email(info, target, rep)
    email_file = os.path.join(artifacts_dir, f"release_email_{target.tag_name}{suffix}.txt")
    with open(email_file, "w", encoding="utf-8") as f:
        f.write(email_text)
    logger.info("Generated release email: %s", email_file)


def _copy_binaries_to_artifacts(target: TargetInfo, build_dir: str, artifacts_dir: str) -> None:
    for binary in [f"{target.target_name}.bin", f"{target.target_name}.map", target.container_name]:
        src = os.path.join(build_dir, binary)
        dst = os.path.join(artifacts_dir, binary)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            logger.info("Copied %s to artifacts directory", binary)


def _commit_binaries(rep: GitlabRep, info: ReleaseInfo, all_binaries: list[str]) -> str:
    if not all_binaries:
        return rep.get_latest_commit_hash(info.branch_name)

    repo_dir = os.getcwd()
    commit_message = "feat(btl.bin): add firmware binaries"
    return rep.commit_and_push_binaries(repo_dir, info.branch_name, all_binaries, commit_message)


def _update_changelog(info: ReleaseInfo, rep: GitlabRep) -> None:
    new_branch = f"feature/changelog-update-{info.targets[0].tag_name}-release"
    commit_message = f"docs(changelog): update for release {info.targets[0].tag_name}"
    ChangelogGenerator().update_changelog_and_push(info, rep, new_branch, commit_message)


def _push_release_binaries(info: ReleaseInfo, build_dir: str) -> None:
    try:
        fw_repo_url = os.getenv("FW_STORE_REPO_URL")
        fw_token = os.getenv("FW_PUSH_TOKEN")
        fw_branch = os.getenv("FW_STORE_BRANCH", "main")

        if not info.targets:
            logger.warning("No targets found for publishing to firmware store")
            return

        pusher = None
        try:
            if fw_repo_url and fw_token:
                pusher = FirmwareStorePusher(fw_repo_url, fw_token, fw_branch)

            for target in info.targets:
                release_tag = target.tag_name + "-release"
                files_to_push = collect_target_files(build_dir, [target])

                if files_to_push and pusher:
                    try:
                        pusher.push_release(tag_name=release_tag, src_paths=files_to_push)
                    except Exception as exc:
                        logger.error("Publish to firmware store failed for tag %s: %s", release_tag, exc)
                else:
                    logger.warning(
                        "Skip publish for tag %s (no files or credentials). Files: %d, URL set: %s",
                        release_tag, len(files_to_push), bool(fw_repo_url)
                    )
        finally:
            if pusher:
                pusher.close()
    except Exception as exc:
        logger.error("Publish to firmware store failed: %s", exc)


def _process_upgrade_release(info: ReleaseInfo, rep: GitlabRep, build_dir: str) -> None:
    for target in info.targets:
        logger.info("Processing target: %s", target.target_name)
        beta_tag = target.tag_name
        release_tag = beta_tag + "-release"

        validate_tag(release_tag, rep)

        existing_url = rep.get_tag(beta_tag)
        if existing_url is None:
            raise ValueError(f"Beta tag '{beta_tag}' not found")

        commit_hash = rep.get_tag_commit_hash(beta_tag)
        GeneratorTag(rep, release_tag, info.features, info.bug_fixes, commit_hash).generate()

    _update_changelog(info, rep)

    artifacts_dir = _ensure_artifacts_dir(build_dir)
    for target in info.targets:
        _save_release_email(info, target, rep, artifacts_dir, suffix="-release")
    logger.info("All release artifacts are saved in: %s", artifacts_dir)

    _push_release_binaries(info, build_dir)


def _process_regular_release(info: ReleaseInfo, rep: GitlabRep, token: str, build_dir: str) -> None:
    all_binaries: list[str] = []

    for target in info.targets:
        logger.info("Processing target: %s", target.target_name)
        validate_target(target.target_name)
        validate_tag(target.tag_name, rep)
        all_binaries.extend(_build_single_target(target, info, token, build_dir))

    release_commit_hash = _commit_binaries(rep, info, all_binaries)
    artifacts_dir = _ensure_artifacts_dir(build_dir)

    for target in info.targets:
        GeneratorTag(rep, target.tag_name, info.features, info.bug_fixes, release_commit_hash).generate()
        _save_release_email(info, target, rep, artifacts_dir)
        _copy_binaries_to_artifacts(target, build_dir, artifacts_dir)

    logger.info("All release artifacts are saved in: %s", artifacts_dir)


def main() -> NoReturn:
    release_json_path, defs_path = _parse_args()
    token = _get_required_env("RELEASE_TOKEN")

    info = ReleaseParser(release_json_path, defs_path).parse()
    build_dir = 'build'
    rep = GitlabRep(GITLAB_URL, info.git_project_id, token, info, build_dir)

    validate_branch(info)

    if info.upgrade_to_release:
        _process_upgrade_release(info, rep, build_dir)
        sys.exit(0)

    _process_regular_release(info, rep, token, build_dir)
    sys.exit(0)


if __name__ == '__main__':
    main()