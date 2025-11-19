import subprocess
import tempfile
import shutil
import logging
import os
import hashlib
import json
import threading
from datetime import datetime

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

class FirmwareStorePusher:
    """
    Pushes files to a firmware store repository.
    A directory named after the release tag is created in the store and binaries are copied there.

    The implementation is optimized for repeated calls: the repository clone is created lazily
    and reused between instances of this class for the same (auth_url, branch) pair.
    The cloned repository will be removed when the last instance is closed or during cleanup.
    """

    # shared state: map (auth_url, branch) -> { 'tmp': path, 'refs': int }
    _shared_lock = threading.Lock()
    _shared_repos: dict = {}

    def __init__(self, repo_url: str, token: str, branch: str = "main"):
        if not repo_url or not token:
            raise RuntimeError("FW_STORE_REPO_URL/FW_PUSH_TOKEN are required")
        self.repo_url = repo_url
        self.auth_url = repo_url.replace("https://", f"https://oauth2:{token}@", 1)
        self.branch = branch
        # key for shared cache
        self._key = (self.auth_url, self.branch)
        # register reference
        with FirmwareStorePusher._shared_lock:
            entry = FirmwareStorePusher._shared_repos.get(self._key)
            if entry is None:
                FirmwareStorePusher._shared_repos[self._key] = { 'tmp': None, 'refs': 1 }
            else:
                entry['refs'] += 1

    def _run(self, args, cwd=None):
        logging.debug("RUN: %s", " ".join(args))
        subprocess.run(args, cwd=cwd, check=True)

    def _ensure_clone(self):
        """Ensure the repository is cloned into a temporary dir for this auth_url/branch."""
        with FirmwareStorePusher._shared_lock:
            entry = FirmwareStorePusher._shared_repos[self._key]
            if entry['tmp']:
                return entry['tmp']
            # create tmp and clone
            tmp = tempfile.mkdtemp(prefix="fw-store-")
            try:
                user_name = "ci_bot"
                user_email = "ci_bot@unic-lab.by"
                # set global config once (affects environment)
                subprocess.run(['git', 'config', '--global', 'user.email', user_email], check=True)
                subprocess.run(['git', 'config', '--global', 'user.name', user_name], check=True)

                self._run(["git", "clone", "--depth=1", self.auth_url, tmp])
            except Exception:
                # cleanup on failure
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            entry['tmp'] = tmp
            return tmp

    def push_release(self, tag_name: str, src_paths: list[str]) -> None:
        """
        Copy files into <tag_name>/ inside the parsed/reused clone, commit and push.
        Can be called multiple times — the clone will be created only once for the same (repo, branch) pair.
        """
        if not tag_name or not src_paths:
            logger.info("Nothing to publish: tag=%r files=%d", tag_name, len(src_paths) if src_paths is not None else 0)
            return

        tmp = self._ensure_clone()

        dest_dir = os.path.join(tmp, tag_name)
        os.makedirs(dest_dir, exist_ok=True)

        # copy files
        copied = 0
        for p in src_paths:
            if os.path.isfile(p):
                shutil.copy2(p, dest_dir)
                copied += 1
            else:
                logger.warning("File not found, skip: %s", p)
        if copied == 0:
            logger.warning("No files were copied for tag %s", tag_name)

        # checksums
        checksums_path = os.path.join(dest_dir, "checksums.txt")
        with open(checksums_path, "w", encoding="utf-8") as ch:
            for fname in sorted(os.listdir(dest_dir)):
                full = os.path.join(dest_dir, fname)
                if os.path.isfile(full):
                    h = hashlib.sha256()
                    with open(full, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    ch.write(f"{h.hexdigest()}  {fname}\n")

        # build-info
        build_info = {
            "source_project": os.getenv("CI_PROJECT_PATH", ""),
            "pipeline_id": os.getenv("CI_PIPELINE_ID", ""),
            "commit": os.getenv("CI_COMMIT_SHA", ""),
            "tag": tag_name,
            "built_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(os.path.join(dest_dir, "build-info.json"), "w", encoding="utf-8") as bf:
            json.dump(build_info, bf, ensure_ascii=False, indent=2)

        # git add/commit/push
        self._run(["git", "add", "."], cwd=tmp)
        # if there is nothing to commit — exit quietly
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp).returncode == 0:
            logger.info("No changes to commit for tag %s", tag_name)
            return

        msg = f"Add release binaries for {tag_name}"
        self._run(["git", "commit", "-m", msg], cwd=tmp)
        self._run(["git", "push", "origin", f"HEAD:{self.branch}"], cwd=tmp)
        logger.info("Firmware published to store under folder: %s", tag_name)

    def close(self):
        """Decrease refcount and cleanup cloned repo when no more references exist."""
        with FirmwareStorePusher._shared_lock:
            entry = FirmwareStorePusher._shared_repos.get(self._key)
            if not entry:
                return
            entry['refs'] -= 1
            if entry['refs'] <= 0:
                tmp = entry.get('tmp')
                try:
                    if tmp:
                        shutil.rmtree(tmp, ignore_errors=True)
                finally:
                    del FirmwareStorePusher._shared_repos[self._key]

    def __del__(self):
        # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass
