import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import get_settings


class GitServiceError(RuntimeError):
    pass


class GitService:
    def __init__(
        self,
        repository_root: Path | None = None,
        *,
        timeout: int | None = None,
        allow_local_repositories: bool = False,
    ):
        settings = get_settings()
        self.root = Path(repository_root or settings.repository_root).resolve()
        self.timeout = timeout if timeout is not None else settings.git_timeout_seconds
        if self.timeout <= 0:
            raise ValueError("Git timeout must be positive")
        self.allow_local_repositories = allow_local_repositories

    def _path(self, project_slug: str) -> Path:
        if len(project_slug) > 100 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", project_slug):
            raise ValueError("Invalid project slug")
        path = self.root / project_slug
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Repository path must stay inside the repository root")
        if (path / ".git").is_symlink():
            raise ValueError("Git metadata must not be a symbolic link")
        return path

    def _run(self, arguments: list[str], *, cwd: Path | None = None) -> str:
        environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes",
            }
        )
        command = [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "submodule.recurse=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "protocol.ssh.allow=always",
            "-c",
            f"protocol.file.allow={'always' if self.allow_local_repositories else 'never'}",
            *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise GitServiceError("Git operation timed out") from None
        except (subprocess.CalledProcessError, OSError):
            # Git stderr and command arguments can contain credentials; do not expose them.
            raise GitServiceError("Git operation failed") from None
        return result.stdout.strip()

    def repository_exists(self, project_slug: str) -> bool:
        path = self._path(project_slug)
        if not (path / ".git").is_dir():
            return False
        try:
            return Path(self._run(["rev-parse", "--show-toplevel"], cwd=path)).resolve() == path
        except GitServiceError:
            return False

    def _repository(self, project_slug: str) -> Path:
        path = self._path(project_slug)
        if not self.repository_exists(project_slug):
            raise GitServiceError("Repository is missing or invalid")
        return path

    def repository_path(self, project_slug: str) -> Path:
        """Return the validated checkout location for a cloned project."""
        return self._repository(project_slug)

    def _source(self, repository_url: str) -> str:
        if not repository_url or any(
            ord(char) <= 32 or ord(char) == 127 for char in repository_url
        ):
            raise ValueError("Invalid repository URL")
        if self.allow_local_repositories and Path(repository_url).is_dir():
            return str(Path(repository_url).resolve())
        if re.fullmatch(r"git@[a-zA-Z0-9][a-zA-Z0-9.-]*:[a-zA-Z0-9_./-]+", repository_url):
            return repository_url
        try:
            parsed = urlsplit(repository_url)
            valid = (
                parsed.scheme in {"https", "ssh"}
                and parsed.hostname
                and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", parsed.hostname)
                and parsed.path not in {"", "/"}
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and (parsed.scheme == "ssh" or parsed.username is None)
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use an HTTPS or SSH repository URL without embedded passwords")
        return repository_url

    def clone_repository(self, project_slug: str, repository_url: str) -> Path:
        path = self._path(project_slug)
        source = self._source(repository_url)
        if path.exists():
            raise GitServiceError("Repository destination already exists")
        self.root.mkdir(parents=True, exist_ok=True)
        self._run(["clone", "--no-checkout", "--no-local", "--template=", "--", source, str(path)])
        return self._repository(project_slug)

    def fetch_repository(self, project_slug: str) -> None:
        path = self._repository(project_slug)
        self._run(["fetch", "--all", "--prune"], cwd=path)

    def _commit(self, path: Path, commit_sha: str) -> str:
        if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit_sha):
            raise ValueError("An exact full commit SHA is required")
        if self._run(["cat-file", "-t", commit_sha], cwd=path) != "commit":
            raise GitServiceError("SHA does not identify a commit")
        return commit_sha.lower()

    def checkout_commit(self, project_slug: str, commit_sha: str) -> None:
        path = self._repository(project_slug)
        sha = self._commit(path, commit_sha)
        self._run(["checkout", "--detach", "--force", sha], cwd=path)
        self._run(["reset", "--hard", sha], cwd=path)
        self._run(["clean", "-fd"], cwd=path)

    def get_commit_message(self, project_slug: str, commit_sha: str) -> str:
        path = self._repository(project_slug)
        sha = self._commit(path, commit_sha)
        return self._run(["show", "-s", "--format=%B", sha, "--"], cwd=path)

    def get_remote_branch_sha(self, project_slug: str, branch: str) -> str:
        path = self._repository(project_slug)
        if not branch or branch.startswith("-") or branch == "@":
            raise ValueError("Invalid branch name")
        ref = f"refs/heads/{branch}"
        self._run(["check-ref-format", ref], cwd=path)
        output = self._run(["ls-remote", "--exit-code", "--heads", "origin", ref], cwd=path)
        for line in output.splitlines():
            sha, _, name = line.partition("\t")
            if name == ref and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
                return sha
        raise GitServiceError("Remote branch not found")
