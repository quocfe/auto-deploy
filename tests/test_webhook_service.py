import hashlib
import hmac

import pytest

from app.services.webhook_service import (
    WebhookError,
    push_branch,
    push_commit,
    verify_github_signature,
)


def test_validates_github_hmac_signature():
    payload = b'{"ref":"refs/heads/main"}'
    secret = "webhook-secret"
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    verify_github_signature(payload, signature, secret)
    with pytest.raises(WebhookError):
        verify_github_signature(payload, "sha256=" + "0" * 64, secret)
    with pytest.raises(WebhookError):
        verify_github_signature(payload, None, secret)


def test_parses_only_valid_branch_pushes_and_exact_sha():
    payload = {
        "ref": "refs/heads/feature/new-api",
        "after": "a" * 40,
        "head_commit": {"message": "Deploy feature"},
    }
    assert push_branch(payload) == "feature/new-api"
    assert push_commit(payload) == ("a" * 40, "Deploy feature")
    with pytest.raises(WebhookError):
        push_branch({"ref": "refs/tags/v1"})
    with pytest.raises(WebhookError):
        push_commit({"after": "HEAD"})
