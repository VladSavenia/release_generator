import sys
import os
import re
import logging
import sys
import codecs
from typing import NoReturn
from datetime import datetime

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
    # Prefer firmware storage repo for direct links to binaries/containers when configured
    fw_repo = os.getenv("FW_STORE_REPO_URL", "").rstrip('.git')
    fw_branch = os.getenv("FW_STORE_BRANCH", "dev")
    if fw_repo:
        bin_link = f"{fw_repo}/-/blob/{fw_branch}/{tag_name}/{target.target_name}.bin"
        container_link = f"{fw_repo}/-/blob/{fw_branch}/{tag_name}/{target.container_name}"
        changelog_link = f"{fw_repo}/-/blob/{fw_branch}/CHANGELOG.md"
    else:
        bin_link = f"{repo_url}/-/blob/{tag_name}/build/{target.target_name}.bin"
        container_link = f"{repo_url}/-/blob/{tag_name}/build/{target.container_name}"
        changelog_link = f"{repo_url}/-/blob/dev/CHANGELOG.md"

    # Include changelog link only for upgrade-to-release flows (we update changelog in those cases)
    changelog_section = (
        f"Ссылка на CHANGELOG.md:\n{changelog_link}\n\n"
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
{bin_link}

Ссылка на файл контейнера для обновления прошивки (*.btl.bin):
{container_link}

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


def main() -> NoReturn:
    if len(sys.argv) < 3:
        print("Usage: python -m release_generator.release <path_to_release.json> <path_to_defs.h>")
        sys.exit(1)

    release_json_path = os.path.abspath(sys.argv[1])
    defs_path = os.path.abspath(sys.argv[2])
    token = os.getenv("RELEASE_TOKEN")
    if not token:
        logger.error("Missing RELEASE_TOKEN in environment variables")
        sys.exit(1)

    # 1. Parse configuration files
    info = ReleaseParser(release_json_path, defs_path).parse()

    # 2. Initialize repository and perform basic validations
    build_dir = 'build'
    rep = GitlabRep(GITLAB_URL, info.git_project_id, token, info, build_dir)
    validate_branch(info)

    # 3. Process each target combination
    all_binaries = []
    for target in info.targets:
        logger.info("Processing target: %s", target.target_name)

        if info.upgrade_to_release:
            # For upgrade_to_release mode: verify beta-tag exists and create release-tag
            beta_tag = target.tag_name
            release_tag = beta_tag + "-release"
            validate_tag(release_tag, rep)

            existing_url = rep.get_tag(beta_tag)
            if existing_url is None:
                raise ValueError(f"Beta tag '{beta_tag}' not found")

            # Get commit hash from beta-tag
            commit_hash = rep.get_tag_commit_hash(beta_tag)

            # Create release-tag on the same commit
            GeneratorTag(rep, release_tag, info.features, info.bug_fixes, commit_hash).generate()
            continue

        # Validate target and tag for normal mode
        validate_target(target.target_name)
        validate_tag(target.tag_name, rep)

        # 4. Build for current target
        temp_info = ReleaseInfo(
            git_project_id=info.git_project_id,
            branch_name=info.branch_name,
            is_service_firmware=info.is_service_firmware,
            upgrade_to_release=info.upgrade_to_release,
            features=info.features,
            bug_fixes=info.bug_fixes,
            targets=[target]  # only the current target
            )

        build_rep = GitlabRep(GITLAB_URL, info.git_project_id, token, temp_info, build_dir)
        build_rep.build_project()

        # Add binaries to the common list
        bin_name = os.path.join(build_dir, f"{target.target_name}.bin")
        map_name = os.path.join(build_dir, f"{target.target_name}.map")
        container_name = os.path.join(build_dir, target.container_name)
        all_binaries.extend([bin_name, map_name, container_name])

    # 5*. Update changelog, generate email texts and push release binaries to 
    #     a firmware storage repository
    if info.upgrade_to_release:
        # 5.1 Update changelog
        # Create a new branch name for changelog update
        new_branch = f"feature/changelog-update-{info.targets[0].tag_name}-release"

        # Commit message for changelog changes
        commit_message = f"docs(changelog): update for release {info.targets[0].tag_name}"

        # Update CHANGELOG.md
        ChangelogGenerator().update_changelog_and_push(info, rep, new_branch, commit_message)

        # 5.2 Generate release email and save to file
        artifacts_dir = os.path.join(build_dir, 'artifacts')
        os.makedirs(artifacts_dir, exist_ok=True)
        for target in info.targets:
            email_text = generate_release_email(info, target, rep)
            email_file = os.path.join(artifacts_dir, f"release_email_{target.tag_name}-release.txt")
            with open(email_file, "w", encoding="utf-8") as f:
                f.write(email_text)
            logger.info(f"Generated release email: {email_file}")

        logger.info(f"All release artifacts are saved in: {artifacts_dir}")

        # 5.3 push release binaries to a firmware storage repository
        try:
            fw_repo_url   = os.getenv("FW_STORE_REPO_URL")
            fw_token      = os.getenv("FW_PUSH_TOKEN")
            fw_branch     = os.getenv("FW_STORE_BRANCH", "dev")
            fw_project_id = int(os.getenv("FW_STORE_PROJECT_ID", "0"))

            if not info.targets:
                logger.warning("No targets found for publishing to firmware store")
            else:
                # create a single pusher and reuse it for all targets
                pusher = None
                try:
                    if fw_repo_url and fw_token:
                        pusher = FirmwareStorePusher(fw_repo_url, fw_token, fw_branch)

                    for target in info.targets:
                        release_tag = target.tag_name + "-release"  # folder = this tag
                        # collect only files for this target
                        files_to_push = collect_target_files(build_dir, [target])

                        if files_to_push and pusher:
                            try:
                                pusher.push_release(info, tag_name=release_tag, src_paths=files_to_push)
                            except Exception as e:
                                logger.error("Publish to firmware store failed for tag %s: %s", release_tag, e)
                        else:
                            logger.warning(
                                "Skip publish for tag %s (no files or credentials). Files: %d, URL set: %s",
                                release_tag, len(files_to_push), bool(fw_repo_url)
                            )

                    if fw_token and fw_project_id:
                        branch_for_changelog_upd = f"feature/changelog-update-{info.targets[0].tag_name}-release"
                        rep = GitlabRep(GITLAB_URL, fw_project_id, fw_token, info, build_dir)
                        ChangelogGenerator().update_changelog_and_push(info, rep, branch_for_changelog_upd, commit_message="docs: update changelog for new release")
                finally:
                    if pusher:
                        pusher.close()
        except Exception as e:
            logger.error("Publish to firmware store failed: %s", e)

        sys.exit(0)

    # 5. Commit all binaries together
    if all_binaries:
        repo_dir = os.getcwd()
        commit_message = f"feat(btl.bin): add firmware binaries"
        release_commit_hash = rep.commit_and_push_binaries(repo_dir, info.branch_name, all_binaries, commit_message)
    else:
        release_commit_hash = rep.get_latest_commit_hash(info.branch_name)

    # 6. Create tags for each target, generate release emails and push binaries to firmware storage repo
    artifacts_dir = os.path.join(build_dir, 'artifacts')
    os.makedirs(artifacts_dir, exist_ok=True)

    for target in info.targets:
        GeneratorTag(rep, target.tag_name, info.features, info.bug_fixes, release_commit_hash).generate()

        # 6.1 Generate release email and save to file
        email_text = generate_release_email(info, target, rep)
        email_file = os.path.join(artifacts_dir, f"release_email_{target.tag_name}.txt")
        with open(email_file, "w", encoding="utf-8") as f:
            f.write(email_text)
        logger.info(f"Generated release email: {email_file}")

        # 6.2 Copy binaries to artifacts directory
        for binary in [f"{target.target_name}.bin", f"{target.target_name}.map", target.container_name]:
            src = os.path.join(build_dir, binary)
            dst = os.path.join(artifacts_dir, binary)
            if os.path.exists(src):
                import shutil
                shutil.copy2(src, dst)
                logger.info(f"Copied {binary} to artifacts directory")

    logger.info(f"All release artifacts are saved in: {artifacts_dir}")

    # 6.3 push release binaries to a firmware storage repository
    try:
        fw_repo_url   = os.getenv("FW_STORE_REPO_URL")
        fw_token      = os.getenv("FW_PUSH_TOKEN")
        fw_branch     = os.getenv("FW_STORE_BRANCH", "dev")

        if not info.targets:
            logger.warning("No targets found for publishing to firmware store")
        else:
            # create a single pusher and reuse it for all targets
            pusher = None
            try:
                if fw_repo_url and fw_token:
                    pusher = FirmwareStorePusher(fw_repo_url, fw_token, fw_branch)

                for target in info.targets:
                    release_tag = target.tag_name  # folder = this tag
                    # collect only files for this target
                    files_to_push = collect_target_files(build_dir, [target])

                    if files_to_push and pusher:
                        try:
                            pusher.push_release(info, tag_name=release_tag, src_paths=files_to_push)
                        except Exception as e:
                            logger.error("Publish to firmware store failed for tag %s: %s", release_tag, e)
                    else:
                        logger.warning(
                            "Skip publish for tag %s (no files or credentials). Files: %d, URL set: %s",
                            release_tag, len(files_to_push), bool(fw_repo_url)
                        )
            finally:
                if pusher:
                    pusher.close()
    except Exception as e:
        logger.error("Publish to firmware store failed: %s", e)

    sys.exit(0)


if __name__ == '__main__':
    main()