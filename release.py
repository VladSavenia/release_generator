import codecs
import logging
import os
import re
import sys
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


def parse_args() -> tuple[str, str]:
    if len(sys.argv) < 3:
        print("Usage: python -m release_generator.release <path_to_release.json> <path_to_defs.h>")
        sys.exit(1)

    release_json_path = os.path.abspath(sys.argv[1])
    defs_path = os.path.abspath(sys.argv[2])
    return release_json_path, defs_path


def require_token() -> str:
    token = os.getenv("RELEASE_TOKEN")
    if not token:
        logger.error("Missing RELEASE_TOKEN in environment variables")
        sys.exit(1)
    return token


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
    """
    Generate release email text with all necessary information and links.
    
    Args:
        info: Release information object
        target: Target information object
        rep: GitLab repository object
    
    Returns:
        Formatted email text
    """
    # Extract product name from target name
    product_name = target.target_name.split('_hard')[0].upper()
    
    # Collect all Jira tasks
    all_tasks = set()
    for item in info.features + info.bug_fixes:
        match = JIRA_TASK_PATTERN.search(item)
        if match:
            all_tasks.add(match.group(1))
    
    # Format features and bugfixes
    features_text = "- отсутствуют" if not info.features else "\n".join(f"{item}" for item in info.features)
    bugfixes_text = "- отсутствуют" if not info.bug_fixes else "\n".join(f"{item}" for item in info.bug_fixes)
    
    # Get repository URL
    repo_url = rep.get_project_url()
    branch_type = "hotfix" if info.branch_name.startswith("hotfix") else "release"

    # Choose opening phrase depending on whether this is an upgrade-to-release
    intro_phrase = "Для передачи заказчику" if info.upgrade_to_release else "Для передачи на тестирование"

    # Determine tag name: if upgrading to release, append '-release' to base tag.
    # Otherwise, use base tag name.
    tag_name = target.tag_name + "-release" if info.upgrade_to_release else target.tag_name

    # Generate email text
    # Include changelog link only for upgrade-to-release flows (we update changelog in those cases)
    changelog_section = (
        f"Ссылка на CHANGELOG.md:\n{repo_url}/-/blob/dev/CHANGELOG.md\n\n"
        if info.upgrade_to_release
        else ""
    )

    email_text = f"""{intro_phrase}. {product_name} {tag_name}.

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
    return email_text

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


def build_target(target: TargetInfo, info: ReleaseInfo, token: str, build_dir: str, rep: GitlabRep) -> list[str]:
    validate_target(target.target_name)
    validate_tag(target.tag_name, rep)

    temp_info = ReleaseInfo(
        git_project_id=info.git_project_id,
        branch_name=info.branch_name,
        is_service_firmware=info.is_service_firmware,
        upgrade_to_release=info.upgrade_to_release,
        features=info.features,
        bug_fixes=info.bug_fixes,
        targets=[target],
    )

    build_rep = GitlabRep(GITLAB_URL, info.git_project_id, token, temp_info, build_dir)
    build_rep.build_project()

    return [
        os.path.join(build_dir, f"{target.target_name}.bin"),
        os.path.join(build_dir, f"{target.target_name}.map"),
        os.path.join(build_dir, target.container_name),
    ]


def commit_binaries(rep: GitlabRep, branch_name: str, binaries: list[str]) -> str:
    if not binaries:
        return rep.get_latest_commit_hash(branch_name)

    repo_dir = os.getcwd()
    commit_message = "feat(btl.bin): add firmware binaries"
    return rep.commit_and_push_binaries(repo_dir, branch_name, binaries, commit_message)


def write_email(artifacts_dir: str, info: ReleaseInfo, target: TargetInfo, rep: GitlabRep, suffix: str = "") -> None:
    email_text = generate_release_email(info, target, rep)
    email_file = os.path.join(artifacts_dir, f"release_email_{target.tag_name}{suffix}.txt")
    with open(email_file, "w", encoding="utf-8") as f:
        f.write(email_text)
    logger.info("Generated release email: %s", email_file)


def copy_artifacts(build_dir: str, artifacts_dir: str, target: TargetInfo) -> None:
    for binary in [f"{target.target_name}.bin", f"{target.target_name}.map", target.container_name]:
        src = os.path.join(build_dir, binary)
        dst = os.path.join(artifacts_dir, binary)
        if os.path.exists(src):
            import shutil

            shutil.copy2(src, dst)
            logger.info("Copied %s to artifacts directory", binary)


def push_to_firmware_store(build_dir: str, info: ReleaseInfo) -> None:
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
                    except Exception as e:
                        logger.error("Publish to firmware store failed for tag %s: %s", release_tag, e)
                else:
                    logger.warning(
                        "Skip publish for tag %s (no files or credentials). Files: %d, URL set: %s",
                        release_tag,
                        len(files_to_push),
                        bool(fw_repo_url),
                    )
        finally:
            if pusher:
                pusher.close()
    except Exception as e:
        logger.error("Publish to firmware store failed: %s", e)


def handle_upgrade_release(info: ReleaseInfo, rep: GitlabRep, build_dir: str) -> None:
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

    new_branch = f"feature/changelog-update-{info.targets[0].tag_name}-release"
    commit_message = f"docs(changelog): update for release {info.targets[0].tag_name}"
    ChangelogGenerator().update_changelog_and_push(info, rep, new_branch, commit_message)

    artifacts_dir = os.path.join(build_dir, 'artifacts')
    os.makedirs(artifacts_dir, exist_ok=True)
    for target in info.targets:
        write_email(artifacts_dir, info, target, rep, suffix="-release")

    logger.info("All release artifacts are saved in: %s", artifacts_dir)
    push_to_firmware_store(build_dir, info)
    sys.exit(0)


def handle_standard_release(info: ReleaseInfo, token: str, build_dir: str, rep: GitlabRep) -> None:
    all_binaries: list[str] = []
    for target in info.targets:
        logger.info("Processing target: %s", target.target_name)
        all_binaries.extend(build_target(target, info, token, build_dir, rep))

    release_commit_hash = commit_binaries(rep, info.branch_name, all_binaries)

    artifacts_dir = os.path.join(build_dir, 'artifacts')
    os.makedirs(artifacts_dir, exist_ok=True)

    for target in info.targets:
        GeneratorTag(rep, target.tag_name, info.features, info.bug_fixes, release_commit_hash).generate()
        write_email(artifacts_dir, info, target, rep)
        copy_artifacts(build_dir, artifacts_dir, target)

    logger.info("All release artifacts are saved in: %s", artifacts_dir)
    sys.exit(0)


def main() -> NoReturn:
    release_json_path, defs_path = parse_args()
    token = require_token()

    info = ReleaseParser(release_json_path, defs_path).parse()
    build_dir = 'build'
    rep = GitlabRep(GITLAB_URL, info.git_project_id, token, info, build_dir)

    validate_branch(info)

    if info.upgrade_to_release:
        handle_upgrade_release(info, rep, build_dir)

    handle_standard_release(info, token, build_dir, rep)


if __name__ == '__main__':
    main()
