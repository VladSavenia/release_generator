import logging
import subprocess
from datetime import datetime
from typing import List, Optional
import gitlab
import gitlab.exceptions

from gitlab.exceptions import GitlabGetError
from .release_parser import ReleaseInfo

logger = logging.getLogger(__name__)

class GitlabRep:
    def __init__(self, gitlab_url: str, project_id: int, token: str, release_info: ReleaseInfo, build_dir: str):
        self._gitlab_url = gitlab_url
        self._project_id = project_id
        self._token = token
        self.__build_dir = build_dir
        self.__release_info = release_info

    def get_project_obj(self):
        gl = gitlab.Gitlab(self._gitlab_url, private_token=self._token)
        return gl.projects.get(self._project_id)

    # ---------- TAGS ----------

    def get_tag_commit_hash(self, tag_name: str) -> str:
        """
        Возвращает хеш коммита для указанного тега.
        Raises GitlabGetError если тег не найден.
        """
        project = self.get_project_obj()
        tag = project.tags.get(tag_name)
        return tag.commit['id']

    def get_tag(self, name: str) -> Optional[str]:
        """
        Возвращает URL браузерного просмотра тега или None, если тега нет.
        """
        project = self.get_project_obj()
        try:
            tag = project.tags.get(id=name)
        except GitlabGetError:
            logger.info("Tag '%s' not found.", name)
            return None

        # Можно распарсить дату при необходимости
        dt_str = tag.commit['created_at']
        try:
            parsed = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M:%S.%f%z')
            logger.info("Found tag '%s' created at %s", name, parsed.isoformat())
        except Exception:
            logger.debug("Could not parse tag date: %s", dt_str)

        repo_url = f"{project.web_url}/-/blob/{name}"
        return repo_url

    def make_tag(self, name: str, desc: str, ref: str) -> str:
        project = self.get_project_obj()
        tag = project.tags.create({'tag_name': name, 'ref': ref, 'message': desc})
        repo_url = f"{project.web_url}/-/blob/{name}"
        logger.info("Created tag '%s' at %s", name, repo_url)
        return repo_url

    # ---------- BUILD ----------

    def build_project(self) -> None:
        """
        Сборка проекта (CMake+Ninja), опционально SERVICE_FIRMWARE.
        """
        logger.info("Configuring CMake...")
        subprocess.run(["cmake", "-B", self.__build_dir, "-G", "Ninja"], check=True)

        logger.info("Cleaning previous build...")
        subprocess.run(["cmake", "--build", self.__build_dir, "--target=clean"], check=True)

        if self.__release_info.is_service_firmware:
            logger.info("Enabling SERVICE_FIRMWARE=ON")
            subprocess.run(["cmake", "-S", ".", "-B", self.__build_dir, "-DSERVICE_FIRMWARE=ON"], check=True)

        if not self.__release_info.targets:
            raise ValueError("No targets specified for build")

        # В этой версии билдим только первый target из списка
        target = self.__release_info.targets[0]
        logger.info("Building target: %s", target.target_name)
        subprocess.run(["cmake", "--build", self.__build_dir, f"--target={target.target_name}"], check=True)

    # ---------- COMMIT/PUSH ----------

    def commit_and_push_binaries(self, repo_dir: str, branch: str, binaries: List[str], commit_message: str) -> str:
        """
        Коммит и пуш бинарников в заданную ветку. Возвращает SHA коммита.
        """
        project = self.get_project_obj()

        repo_url = project.http_url_to_repo
        token = self._token
        authenticated_repo_url = repo_url.replace('https://', f'https://oauth2:{token}@')

        # git config
        user_name = "release_bot"
        user_email = "release_bot@unic-lab.by"
        subprocess.run(['git', 'config', 'user.email', user_email], check=True, cwd=repo_dir)
        subprocess.run(['git', 'config', 'user.name', user_name], check=True, cwd=repo_dir)

        # remote: гарантированно обновим (remove -> add)
        subprocess.run(['git', 'remote', 'remove', 'gitlab_origin'], cwd=repo_dir, check=False)
        subprocess.run(['git', 'remote', 'add', 'gitlab_origin', authenticated_repo_url], check=True, cwd=repo_dir)

        # add
        for binary in binaries:
            logger.info("Adding file: %s", binary)
            subprocess.run(['git', 'add', binary], check=True, cwd=repo_dir)

        # если нечего коммитить — не падаем
        status = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_dir)
        if status.returncode == 0:
            logger.info("No staged changes; skipping commit. Using current HEAD hash.")
        else:
            subprocess.run(['git', 'commit', '-m', commit_message], check=True, cwd=repo_dir)

        # получаем hash
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, cwd=repo_dir, capture_output=True, text=True)
        commit_hash = result.stdout.strip()
        logger.info("Local commit hash: %s", commit_hash)

        # push
        subprocess.run(['git', 'push', 'gitlab_origin', f'HEAD:{branch}'], check=True, cwd=repo_dir)
        logger.info("Pushed to %s:%s", 'gitlab_origin', branch)

        return commit_hash

    def commit_and_push_changelog(self, branch_name: str, files: List[str], commit_message: str) -> str:
        """
        Commits and pushes files using GitLab API instead of git commands.
        Returns the commit hash.
        """
        project = self.get_project_obj()

        try:
            # Try to create branch if it doesn't exist
            try:
                project.branches.create({
                    'branch': branch_name,
                    'ref': project.default_branch
                })
                logger.info(f"Created new branch: {branch_name}")
            except gitlab.exceptions.GitlabCreateError:
                logger.info(f"Branch {branch_name} already exists")

            # Process each file
            for file_path in files:
                logger.info(f"Processing file: {file_path}")

                # Read file content
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                file_name = 'CHANGELOG.md'
                file_data = {
                    'branch': branch_name,
                    'commit_message': commit_message,
                    'content': content,
                }

                # Determine if file exists and handle accordingly
                file_exists = True
                try:
                    file_obj = project.files.get(file_path=file_name, ref=branch_name)
                except gitlab.exceptions.GitlabGetError:
                    file_exists = False

                if not file_exists:
                    # Create new file
                    project.files.create(file_data | {'file_path': file_name})
                    logger.info(f"Created file {file_name} in branch {branch_name}")
                else:
                    # Update existing file
                    file_obj.content = content
                    file_obj.save(branch=branch_name, commit_message=commit_message)
                    logger.info(f"Updated file {file_name} in branch {branch_name}")

            # Get the commit hash of the latest commit
            branch = project.branches.get(branch_name)
            commit_hash = branch.commit['id']
            logger.info(f"Latest commit hash in branch {branch_name}: {commit_hash}")

            return commit_hash

        except Exception as e:
            logger.error(f"Failed to commit and push files: {e}")
            raise

    # ---------- BRANCH ----------

    def get_latest_commit_hash(self, branch_name: str) -> str:
        """
        Возвращает SHA последнего коммита в указанной ветке (через GitLab API).
        """
        project = self.get_project_obj()
        branch = project.branches.get(branch_name)
        sha = branch.commit['id']
        logger.info("Latest commit in '%s': %s", branch_name, sha)
        return sha

    def get_project_url(self) -> str:
        """
        Возвращает URL проекта в GitLab.
        """
        return f"{self._gitlab_url}{self._project_id}"

    def create_branch(self, branch_name: str) -> None:
        """
        Создает новую ветку из текущего состояния main/master.
        """
        project = self.get_project_obj()
        try:
            # Try to get default branch (usually main or master)
            default_branch = project.default_branch
        except Exception as e:
            logger.warning(f"Could not determine default branch: {e}")
            default_branch = 'main'

        try:
            project.branches.create({
                'branch': branch_name,
                'ref': default_branch
            })
            logger.info(f"Created new branch: {branch_name}")
        except Exception as e:
            logger.error(f"Failed to create branch {branch_name}: {e}")
            raise
