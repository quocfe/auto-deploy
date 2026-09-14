from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dashboard import templates
from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import hash_password, verify_password
from app.models import User

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]


def require_api_login(request: Request) -> None:
    if not request.session.get("user_id"):
        raise HTTPException(401, "Login required")


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.username == username))
    if user is not None:
        return user if verify_password(password, user.password_hash) else None
    settings = get_settings()
    configured_password = settings.admin_password
    if (
        settings.admin_username != username
        or configured_password is None
        or password != configured_password.get_secret_value()
    ):
        return None
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login", include_in_schema=False)
async def login(
    request: Request,
    session: Session,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = await authenticate(session, username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid credentials"}, 401
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/api/auth/login")
async def api_login(
    request: Request,
    session: Session,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = await authenticate(session, username, password)
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    request.session["user_id"] = user.id
    return {"status": "ok"}


@router.post("/api/auth/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"status": "ok"}
