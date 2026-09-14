from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.repositories.deployment_repository import DeploymentRepository
from app.repositories.environment_repository import EnvironmentRepository
from app.repositories.environment_variable_repository import EnvironmentVariableRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.management import (
    EnvironmentCreate,
    EnvironmentUpdate,
    EnvironmentVariableCreate,
    ProjectCreate,
    ProjectUpdate,
)
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerService
from app.services.encryption_service import EncryptionServiceError
from app.services.git_service import GitService, GitServiceError

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
Session = Annotated[AsyncSession, Depends(get_session)]


def require_login(request: Request) -> None:
    if not request.session.get("user_id"):
        raise HTTPException(401, "Login required")


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def checkbox(value: str | None) -> bool:
    return value is not None


def form_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(item["msg"] for item in error.errors())
    return "A record with that name already exists."


@router.get("/", include_in_schema=False)
async def dashboard(request: Request, session: Session):
    require_login(request)
    return templates.TemplateResponse(
        request, "dashboard.html", {"projects": await ProjectRepository(session).list()}
    )


@router.get("/projects/new", include_in_schema=False)
async def new_project_page(request: Request):
    require_login(request)
    return templates.TemplateResponse(request, "project_form.html", {"project": None})


@router.post("/projects/new", include_in_schema=False)
async def create_project_page(
    request: Request,
    session: Session,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    repository_url: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "github",
):
    require_login(request)
    try:
        project = await ProjectRepository(session).create(
            ProjectCreate(name=name, slug=slug, repository_url=repository_url, provider=provider)
        )
        await session.commit()
    except (ValidationError, IntegrityError) as error:
        await session.rollback()
        return templates.TemplateResponse(
            request,
            "project_form.html",
            {"project": None, "error": form_error(error), "values": request.query_params},
            422,
        )
    return redirect(f"/projects/{project.id}")


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


@router.get("/projects/{project_id}/edit", include_in_schema=False)
async def edit_project_page(project_id: int, request: Request, session: Session):
    require_login(request)
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return templates.TemplateResponse(request, "project_form.html", {"project": project})


@router.post("/projects/{project_id}/edit", include_in_schema=False)
async def update_project_page(
    project_id: int,
    request: Request,
    session: Session,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    repository_url: Annotated[str, Form()],
    provider: Annotated[str, Form()],
):
    require_login(request)
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    try:
        await ProjectRepository(session).update(
            project,
            ProjectUpdate(name=name, slug=slug, repository_url=repository_url, provider=provider),
        )
        await session.commit()
    except (ValidationError, IntegrityError) as error:
        await session.rollback()
        return templates.TemplateResponse(
            request, "project_form.html", {"project": project, "error": form_error(error)}, 422
        )
    return redirect(f"/projects/{project_id}")


@router.get("/projects/{project_id}/environments/new", include_in_schema=False)
async def new_environment_page(project_id: int, request: Request, session: Session):
    require_login(request)
    if await ProjectRepository(session).get(project_id) is None:
        raise HTTPException(404, "Project not found")
    return templates.TemplateResponse(
        request, "environment_form.html", {"project_id": project_id, "environment": None}
    )


@router.post("/projects/{project_id}/environments/new", include_in_schema=False)
async def create_environment_page(
    project_id: int,
    request: Request,
    session: Session,
    name: Annotated[str, Form()],
    branch: Annotated[str, Form()],
    container_port: Annotated[int, Form()],
    container_name: Annotated[str, Form()],
    dockerfile: Annotated[str, Form()] = "Dockerfile",
    build_context: Annotated[str, Form()] = ".",
    docker_network: Annotated[str, Form()] = "web_network",
    domain: Annotated[str | None, Form()] = None,
    auto_deploy: Annotated[str | None, Form()] = None,
    enabled: Annotated[str | None, Form()] = None,
):
    require_login(request)
    if await ProjectRepository(session).get(project_id) is None:
        raise HTTPException(404, "Project not found")
    try:
        environment = await EnvironmentRepository(session).create(
            project_id,
            EnvironmentCreate(
                name=name,
                branch=branch,
                container_port=container_port,
                container_name=container_name,
                dockerfile=dockerfile,
                build_context=build_context,
                docker_network=docker_network,
                domain=domain or None,
                auto_deploy=checkbox(auto_deploy),
                enabled=checkbox(enabled),
            ),
        )
        await session.commit()
    except (ValidationError, IntegrityError) as error:
        await session.rollback()
        return templates.TemplateResponse(
            request,
            "environment_form.html",
            {"project_id": project_id, "environment": None, "error": form_error(error)},
            422,
        )
    return redirect(f"/environments/{environment.id}/dashboard")


@router.get("/environments/{environment_id}/dashboard", include_in_schema=False)
async def environment_page(environment_id: int, request: Request, session: Session):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    deployments = await DeploymentRepository(session).list_for_environment(environment_id)
    return templates.TemplateResponse(
        request,
        "environment.html",
        {"environment": environment, "deployments": deployments},
    )


@router.get("/environments/{environment_id}/edit", include_in_schema=False)
async def edit_environment_page(environment_id: int, request: Request, session: Session):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    return templates.TemplateResponse(
        request,
        "environment_form.html",
        {"project_id": environment.project_id, "environment": environment},
    )


@router.post("/environments/{environment_id}/edit", include_in_schema=False)
async def update_environment_page(
    environment_id: int,
    request: Request,
    session: Session,
    name: Annotated[str, Form()],
    branch: Annotated[str, Form()],
    container_port: Annotated[int, Form()],
    container_name: Annotated[str, Form()],
    dockerfile: Annotated[str, Form()],
    build_context: Annotated[str, Form()],
    docker_network: Annotated[str, Form()],
    domain: Annotated[str | None, Form()] = None,
    auto_deploy: Annotated[str | None, Form()] = None,
    enabled: Annotated[str | None, Form()] = None,
):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    try:
        await EnvironmentRepository(session).update(
            environment,
            EnvironmentUpdate(
                name=name,
                branch=branch,
                container_port=container_port,
                container_name=container_name,
                dockerfile=dockerfile,
                build_context=build_context,
                docker_network=docker_network,
                domain=domain or None,
                auto_deploy=checkbox(auto_deploy),
                enabled=checkbox(enabled),
            ),
        )
        await session.commit()
    except (ValidationError, IntegrityError) as error:
        await session.rollback()
        return templates.TemplateResponse(
            request,
            "environment_form.html",
            {
                "project_id": environment.project_id,
                "environment": environment,
                "error": form_error(error),
            },
            422,
        )
    return redirect(f"/environments/{environment_id}/dashboard")


@router.get("/environments/{environment_id}/variables/dashboard", include_in_schema=False)
async def environment_variables_page(environment_id: int, request: Request, session: Session):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    variables = await EnvironmentVariableRepository(session).list(environment_id)
    return templates.TemplateResponse(
        request,
        "variables.html",
        {"environment": environment, "variables": variables},
    )


@router.post("/environments/{environment_id}/variables/dashboard", include_in_schema=False)
async def create_environment_variable_page(
    environment_id: int,
    request: Request,
    session: Session,
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
    is_secret: Annotated[str | None, Form()] = None,
):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    repository = EnvironmentVariableRepository(session)
    try:
        await repository.create(
            environment_id,
            EnvironmentVariableCreate(key=key, value=value, is_secret=checkbox(is_secret)),
        )
        await session.commit()
    except (ValidationError, IntegrityError, ValueError, EncryptionServiceError) as error:
        await session.rollback()
        return templates.TemplateResponse(
            request,
            "variables.html",
            {
                "environment": environment,
                "variables": await repository.list(environment_id),
                "error": form_error(error),
            },
            422,
        )
    return redirect(f"/environments/{environment_id}/variables/dashboard")


@router.post("/environments/{environment_id}/deploy", include_in_schema=False)
async def deploy_environment_page(environment_id: int, request: Request, session: Session):
    require_login(request)
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    if not environment.enabled:
        raise HTTPException(409, "Environment is disabled")
    await session.refresh(environment, attribute_names=["project"])
    try:
        await DeploymentService(session, GitService(), DockerService()).queue_manual(environment)
        await session.commit()
    except (GitServiceError, ValueError) as error:
        await session.rollback()
        raise HTTPException(422, "Unable to queue deployment") from error
    return redirect(f"/environments/{environment_id}/dashboard")


@router.post("/environment-variables/{variable_id}/delete", include_in_schema=False)
async def delete_environment_variable_page(variable_id: int, request: Request, session: Session):
    require_login(request)
    repository = EnvironmentVariableRepository(session)
    variable = await repository.get(variable_id)
    if variable is None:
        raise HTTPException(404, "Environment variable not found")
    environment_id = variable.environment_id
    await repository.delete(variable)
    await session.commit()
    return redirect(f"/environments/{environment_id}/variables/dashboard")


@router.get("/deployments/{deployment_id}/dashboard-status", include_in_schema=False)
async def deployment_status(deployment_id: int, request: Request, session: Session):
    require_login(request)
    deployment = await DeploymentRepository(session).get(deployment_id)
    if deployment is None:
        raise HTTPException(404, "Deployment not found")
    return templates.TemplateResponse(request, "deployment_status.html", {"deployment": deployment})


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
