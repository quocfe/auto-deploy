import subprocess

import pytest

from app.services.git_service import GitService, GitServiceError


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, timeout=20
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Test Author")
    git(source, "config", "user.email", "test@example.com")
    (source / "app.txt").write_text("first", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "-m", "Initial commit")
    first = git(source, "rev-parse", "HEAD")
    service = GitService(tmp_path / "repos", allow_local_repositories=True)
    return source, first, service


def test_clone_and_exact_checkout(repository):
    source, first, service = repository
    assert service.repository_exists("app") is False
    path = service.clone_repository("app", str(source))
    assert service.repository_exists("app")
    service.checkout_commit("app", first)
    assert git(path, "rev-parse", "HEAD") == first
    assert (path / "app.txt").read_text() == "first"
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert service.get_commit_message("app", first) == "Initial commit"
    assert service.get_remote_branch_sha("app", "main") == first


def test_fetch_checkout_old_commit_and_clean(repository):
    source, first, service = repository
    path = service.clone_repository("app", str(source))
    service.checkout_commit("app", first)
    (source / "app.txt").write_text("second", encoding="utf-8")
    git(source, "commit", "-am", "Second commit")
    second = git(source, "rev-parse", "HEAD")
    assert service.get_remote_branch_sha("app", "main") == second
    service.fetch_repository("app")
    service.checkout_commit("app", second)
    assert (path / "app.txt").read_text() == "second"
    (path / "app.txt").write_text("dirty", encoding="utf-8")
    (path / "untracked").mkdir()
    (path / "untracked" / "file").write_text("remove me", encoding="utf-8")
    service.checkout_commit("app", first)
    assert git(path, "rev-parse", "HEAD") == first
    assert (path / "app.txt").read_text() == "first"
    assert not (path / "untracked").exists()


def test_fetch_prunes_deleted_remote_branches(repository):
    source, _, service = repository
    git(source, "branch", "temporary")
    path = service.clone_repository("app", str(source))
    assert git(path, "rev-parse", "--verify", "refs/remotes/origin/temporary")
    git(source, "branch", "-D", "temporary")
    service.fetch_repository("app")
    assert "origin/temporary" not in git(path, "branch", "-r")


def test_duplicate_clone_preserves_repository(repository):
    source, first, service = repository
    path = service.clone_repository("app", str(source))
    service.checkout_commit("app", first)
    with pytest.raises(GitServiceError, match="already exists"):
        service.clone_repository("app", str(source))
    assert (path / "app.txt").read_text() == "first"


def test_existing_non_repository_is_not_overwritten(repository):
    source, _, service = repository
    path = service.root / "app"
    path.mkdir(parents=True)
    (path / "keep").write_text("existing data")
    assert not service.repository_exists("app")
    with pytest.raises(GitServiceError, match="already exists"):
        service.clone_repository("app", str(source))
    assert (path / "keep").read_text() == "existing data"


@pytest.mark.parametrize("sha", ["HEAD", "main", "abc123", "--help", "a" * 39, "g" * 40])
def test_rejects_non_exact_commit_before_changes(repository, sha):
    source, first, service = repository
    path = service.clone_repository("app", str(source))
    service.checkout_commit("app", first)
    (path / "app.txt").write_text("keep dirty")
    with pytest.raises(ValueError, match="full commit SHA"):
        service.checkout_commit("app", sha)
    assert (path / "app.txt").read_text() == "keep dirty"


def test_missing_commit_and_non_commit_objects_preserve_checkout(repository):
    source, first, service = repository
    path = service.clone_repository("app", str(source))
    service.checkout_commit("app", first)
    blob = git(path, "rev-parse", "HEAD:app.txt")
    (path / "app.txt").write_text("keep dirty")
    for sha in ["0" * 40, blob]:
        with pytest.raises(GitServiceError):
            service.checkout_commit("app", sha)
    assert (path / "app.txt").read_text() == "keep dirty"


def test_missing_repository_and_remote_branch(repository):
    source, _, service = repository
    with pytest.raises(GitServiceError, match="missing"):
        service.fetch_repository("missing")
    service.clone_repository("app", str(source))
    with pytest.raises(GitServiceError):
        service.get_remote_branch_sha("app", "missing")
    with pytest.raises((ValueError, GitServiceError)):
        service.get_remote_branch_sha("app", "bad..branch")


@pytest.mark.parametrize("slug", ["../escape", "/tmp", "", "a/b", "a\\b", "-option", "Upper"])
def test_invalid_slug(tmp_path, slug):
    service = GitService(tmp_path / "repos")
    with pytest.raises(ValueError):
        service.repository_exists(slug)


def test_symlink_repository_is_rejected(repository):
    source, _, service = repository
    service.root.mkdir()
    (service.root / "app").symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError):
        service.repository_exists("app")


@pytest.mark.parametrize(
    "url",
    [
        "--upload-pack=evil",
        "ext::sh -c evil",
        "file:///tmp/repo",
        "http://example.com/repo",
        "https://user:token@example.com/repo",
        "https://example.com/repo\nnext",
    ],
)
def test_invalid_repository_url(tmp_path, url):
    service = GitService(tmp_path / "repos")
    with pytest.raises(ValueError):
        service.clone_repository("app", url)
    assert not service.root.exists()


def test_local_repositories_require_explicit_opt_in(repository):
    source, _, service = repository
    with pytest.raises(ValueError):
        GitService(service.root).clone_repository("app", str(source))


def test_subprocess_timeout_and_credential_safe_errors(tmp_path, monkeypatch):
    service = GitService(tmp_path / "repos", timeout=7)
    seen = []

    def fail(command, **kwargs):
        seen.append((command, kwargs))
        raise subprocess.CalledProcessError(1, command, stderr="private-token")

    monkeypatch.setenv("GIT_DIR", "/unrelated/repository")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(GitServiceError) as error:
        service.clone_repository("app", "https://example.com/repo.git")
    assert "private-token" not in str(error.value)
    assert seen[0][1]["shell"] is False
    assert seen[0][1]["timeout"] == 7
    assert "GIT_DIR" not in seen[0][1]["env"]
    assert seen[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 7)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(GitServiceError, match="timed out"):
        service.clone_repository("app", "https://example.com/repo.git")
