import hashlib
import hmac
import re


class WebhookError(ValueError):
    pass


def verify_github_signature(payload: bytes, signature: str | None, secret: str | None) -> None:
    if not secret or not signature or not signature.startswith("sha256="):
        raise WebhookError("Missing or invalid GitHub webhook signature")
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("Invalid GitHub webhook signature")


def push_branch(payload: dict) -> str:
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        raise WebhookError("Webhook is not a branch push")
    branch = ref.removeprefix("refs/heads/")
    if not branch or branch.startswith("-") or not re.fullmatch(r"[^\s~^:?*\\[\\]+", branch):
        raise WebhookError("Invalid pushed branch")
    return branch


def push_commit(payload: dict) -> tuple[str, str | None]:
    sha = payload.get("after")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise WebhookError("Webhook does not contain an exact commit SHA")
    head_commit = payload.get("head_commit")
    message = head_commit.get("message") if isinstance(head_commit, dict) else None
    return sha.lower(), message if isinstance(message, str) else None
