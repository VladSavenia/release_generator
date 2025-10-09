import sys
import os
import re
import logging
from typing import NoReturn
from datetime import datetime

from .release_parser import ReleaseParser, ReleaseInfo
from .gitlab_rep import GitlabRep
from .generator_tag import GeneratorTag
from .release_formatter import ReleaseFormatter
from .changelog_generator import ChangelogGenerator

GITLAB_URL = 'https://gitlab.neroelectronics.by/'

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------- validation helpers ----------
_TAG_VRANGE_RE = re.compile(
    r"^v"
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # первая группа
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # вторая группа
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)\."   # третья группа
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)"     # четвёртая группа
    r"-Rev"
    r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]\d?)"    # ревизия
    r"(-release)?$"                          # необязательная часть
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
    # допустимы ветки release или hotfix (с подветками)
    if not re.match(r'^(release|hotfix)(/.*)?$', info.branch_name):
        raise ValueError(
            f"Invalid branch: {info.branch_name}. Allowed branches must start with 'release' or 'hotfix'."
        )
    logger.info("Branch validation passed: %s", info.branch_name)


def validate_tag(tag_name: str, rep: GitlabRep) -> None:
    # 1) формат и диапазоны
    if not _TAG_VRANGE_RE.match(tag_name):
        raise ValueError(
            f"Invalid tag format: {tag_name}, "
            f"expected vXX.XX.XX.XX-RevXX[-release] where XX in 1..255"
        )
    # доп. однородная проверка общим форматтером
    if not ReleaseFormatter.validate_tag_string(tag_name):
        raise ValueError(f"Tag does not match canonical pattern: {tag_name}")

    # 2) существование тега в GitLab
    existing_url = rep.get_tag(tag_name)   # вернёт URL или None
    if existing_url is not None:
        # Тег уже есть — значит релиз уже создан
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

    # 1. Парсинг
    info = ReleaseParser(release_json_path, defs_path).parse()

    # 2. Иниицализация репозитория и базовые валидации
    build_dir = 'build'
    rep = GitlabRep(GITLAB_URL, info.git_project_id, token, info, build_dir)
    validate_branch(info)

    # 3. Обработка каждой target комбинации
    all_binaries = []
    for target in info.targets:
        logger.info("Processing target: %s", target.target_name)

        if info.upgrade_to_release:
            # Для upgrade_to_release проверяем существование beta-тега и создаем release-тег
            beta_tag = target.tag_name
            release_tag = beta_tag + "-release"
            validate_tag(release_tag, rep)

            existing_url = rep.get_tag(beta_tag)
            if existing_url is None:
                raise ValueError(f"Beta tag '{beta_tag}' not found")

            # Получаем коммит из beta-тега
            commit_hash = rep.get_tag_commit_hash(beta_tag)

            # Создаем release-тег на том же коммите
            GeneratorTag(rep, release_tag, info.features, info.bug_fixes, commit_hash).generate()
            continue

        # Валидация target и тега для обычного режима
        validate_target(target.target_name)
        validate_tag(target.tag_name, rep)

        # 4. Build для текущего target
        temp_info = ReleaseInfo(
            git_project_id=info.git_project_id,
            branch_name=info.branch_name,
            is_service_firmware=info.is_service_firmware,
            upgrade_to_release=info.upgrade_to_release,
            features=info.features,
            bug_fixes=info.bug_fixes,
            targets=[target]  # только текущий target
            )

        build_rep = GitlabRep(GITLAB_URL, info.git_project_id, token, temp_info, build_dir)
        build_rep.build_project()

        # Добавляем бинарники в общий список
        bin_name = os.path.join(build_dir, f"{target.target_name}.bin")
        map_name = os.path.join(build_dir, f"{target.target_name}.map")
        container_name = os.path.join(build_dir, target.container_name)
        all_binaries.extend([bin_name, map_name, container_name])

    if info.upgrade_to_release:
        # Create a new branch name for changelog update
        new_branch = f"feature/changelog-update-{info.targets[0].tag_name}-release"

        # Commit message for changelog changes
        commit_message = f"docs(changelog): update for release {info.targets[0].tag_name}"

        # Update CHANGELOG.md
        ChangelogGenerator().update_changelog_and_push(info, rep, new_branch, commit_message)

        sys.exit(0)

    # 5. Общий коммит всех бинарников
    if all_binaries:
        repo_dir = os.getcwd()
        commit_message = f"feat(btl.bin): add firmware binaries"
        release_commit_hash = rep.commit_and_push_binaries(repo_dir, info.branch_name, all_binaries, commit_message)
    else:
        release_commit_hash = rep.get_latest_commit_hash(info.branch_name)

    # 6. Создание тегов для каждого target
    for target in info.targets:
        GeneratorTag(rep, target.tag_name, info.features, info.bug_fixes, release_commit_hash).generate()

    sys.exit(0)


if __name__ == '__main__':
    main()