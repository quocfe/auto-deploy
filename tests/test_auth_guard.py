from fastapi import HTTPException

from app.api.auth import require_api_login


def test_management_auth_guard_requires_session_user():
    request = type("Request", (), {"session": {}})()
    try:
        require_api_login(request)
    except HTTPException as error:
        assert error.status_code == 401
    else:
        raise AssertionError("Expected unauthenticated request to be rejected")


def test_management_auth_guard_accepts_authenticated_session():
    require_api_login(type("Request", (), {"session": {"user_id": 1}})())
