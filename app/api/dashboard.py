from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.repositories.deployment_repository import DeploymentRepository
from app.repositories.environment_repository import EnvironmentRepository
from app.repositories.project_repository import ProjectRepository

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
Session = Annotated[AsyncSession, Depends(get_session)]


def require_login(request: Request) -> None:
    if not request.session.get("user_id"):
        raise HTTPException(401, "Login required")


@router.get("/", include_in_schema=False)
async def dashboard(request: Request, session: Session):
    require_login(request)
    return templates.TemplateResponse(
        request, "dashboard.html", {"projects": await ProjectRepository(session).list()}
    )


@router.get("/projects/{project_id}", include_in_schema=False)
async def project_page(project_id: int, request: Request, session: Session):
    require_login(request)
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    environments = await EnvironmentRepository(session).list(project_id)
    return templates.TemplateResponse(
        request, "project.html", {"project": project, "environments": environments}
    )


@router.get("/environments/{environment_id}/dashboard", include_in_schema=False)
async def environment_page(environment_id: int, request: Request, session: Session):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    return templates.TemplateResponse(request, "environment.html", {"environment": environment})


@router.get("/deployments/{deployment_id}/dashboard", include_in_schema=False)
async def deployment_page(deployment_id: int, request: Request, session: Session):
    require_login(request)
    repository = DeploymentRepository(session)
    deployment = await repository.get(deployment_id)
    if deployment is None:
        raise HTTPException(404, "Deployment not found")
    return templates.TemplateResponse(
        request,
        "deployment.html",
        {"deployment": deployment, "logs": await repository.list_logs(deployment_id)},
    )


@router.get("/logout", include_in_schema=False)
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
