from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_login
from app.core.database import get_session
from app.repositories.deployment_repository import DeploymentRepository
from app.repositories.environment_repository import EnvironmentRepository
from app.repositories.environment_variable_repository import EnvironmentVariableRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.management import (
    DeploymentLogRead,
    DeploymentRead,
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentUpdate,
    EnvironmentVariableCreate,
    EnvironmentVariableRead,
    EnvironmentVariableUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerService
from app.services.encryption_service import EncryptionServiceError
from app.services.git_service import GitService, GitServiceError

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_login)])
Session = Annotated[AsyncSession, Depends(get_session)]


async def persist[T](session: AsyncSession, operation: Awaitable[T]) -> T:
    try:
        result = await operation
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Conflicting name or related resource changed") from exc


async def require_project(session: AsyncSession, project_id: int):
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


async def require_environment(session: AsyncSession, environment_id: int):
    environment = await EnvironmentRepository(session).get(environment_id)
    if environment is None:
        raise HTTPException(404, "Environment not found")
    return environment


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(session: Session):
    return await ProjectRepository(session).list()


@router.post("/projects", response_model=ProjectRead, status_code=201)
async def create_project(data: ProjectCreate, session: Session):
    return await persist(session, ProjectRepository(session).create(data))


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project(project_id: int, session: Session):
    return await require_project(session, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(project_id: int, data: ProjectUpdate, session: Session):
    project = await require_project(session, project_id)
    return await persist(session, ProjectRepository(session).update(project, data))


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: int, session: Session):
    project = await require_project(session, project_id)
    await persist(session, ProjectRepository(session).delete(project))
    return Response(status_code=204)


@router.get("/projects/{project_id}/environments", response_model=list[EnvironmentRead])
async def list_environments(project_id: int, session: Session):
    await require_project(session, project_id)
    return await EnvironmentRepository(session).list(project_id)


@router.post("/projects/{project_id}/environments", response_model=EnvironmentRead, status_code=201)
async def create_environment(project_id: int, data: EnvironmentCreate, session: Session):
    await require_project(session, project_id)
    return await persist(session, EnvironmentRepository(session).create(project_id, data))


@router.get("/environments/{environment_id}", response_model=EnvironmentRead)
async def get_environment(environment_id: int, session: Session):
    return await require_environment(session, environment_id)


@router.patch("/environments/{environment_id}", response_model=EnvironmentRead)
async def update_environment(environment_id: int, data: EnvironmentUpdate, session: Session):
    environment = await require_environment(session, environment_id)
    return await persist(session, EnvironmentRepository(session).update(environment, data))


@router.delete("/environments/{environment_id}", status_code=204)
async def delete_environment(environment_id: int, session: Session):
    environment = await require_environment(session, environment_id)
    await persist(session, EnvironmentRepository(session).delete(environment))
    return Response(status_code=204)


@router.get(
    "/environments/{environment_id}/variables", response_model=list[EnvironmentVariableRead]
)
async def list_environment_variables(environment_id: int, session: Session):
    await require_environment(session, environment_id)
    return await EnvironmentVariableRepository(session).list(environment_id)


@router.post(
    "/environments/{environment_id}/variables",
    response_model=EnvironmentVariableRead,
    status_code=201,
)
async def create_environment_variable(
    environment_id: int, data: EnvironmentVariableCreate, session: Session
):
    await require_environment(session, environment_id)
    try:
        return await persist(
            session, EnvironmentVariableRepository(session).create(environment_id, data)
        )
    except EncryptionServiceError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/environment-variables/{variable_id}", response_model=EnvironmentVariableRead)
async def update_environment_variable(
    variable_id: int, data: EnvironmentVariableUpdate, session: Session
):
    variable = await EnvironmentVariableRepository(session).get(variable_id)
    if variable is None:
        raise HTTPException(404, "Environment variable not found")
    try:
        return await persist(session, EnvironmentVariableRepository(session).update(variable, data))
    except EncryptionServiceError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/environment-variables/{variable_id}", status_code=204)
async def delete_environment_variable(variable_id: int, session: Session):
    variable = await EnvironmentVariableRepository(session).get(variable_id)
    if variable is None:
        raise HTTPException(404, "Environment variable not found")
    await persist(session, EnvironmentVariableRepository(session).delete(variable))
    return Response(status_code=204)


@router.get("/environments/{environment_id}/deployments", response_model=list[DeploymentRead])
async def list_environment_deployments(environment_id: int, session: Session):
    await require_environment(session, environment_id)
    return await DeploymentRepository(session).list_for_environment(environment_id)


@router.post(
    "/environments/{environment_id}/deploy", response_model=DeploymentRead, status_code=202
)
async def deploy_environment(environment_id: int, session: Session):
    environment = await require_environment(session, environment_id)
    if not environment.enabled:
        raise HTTPException(409, "Environment is disabled")
    await session.refresh(environment, attribute_names=["project"])
    try:
        deployment = await DeploymentService(session, GitService(), DockerService()).queue_manual(
            environment
        )
        await session.commit()
    except (GitServiceError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(422, "Unable to resolve deployment commit") from exc
    return deployment


@router.get("/deployments/{deployment_id}", response_model=DeploymentRead)
async def get_deployment(deployment_id: int, session: Session):
    deployment = await DeploymentRepository(session).get(deployment_id)
    if deployment is None:
        raise HTTPException(404, "Deployment not found")
    return deployment


@router.get("/deployments/{deployment_id}/logs", response_model=list[DeploymentLogRead])
async def list_deployment_logs(deployment_id: int, session: Session):
    if await DeploymentRepository(session).get(deployment_id) is None:
        raise HTTPException(404, "Deployment not found")
    return await DeploymentRepository(session).list_logs(deployment_id)


@router.post(
    "/deployments/{deployment_id}/rollback", response_model=DeploymentRead, status_code=201
)
async def rollback_deployment(deployment_id: int, session: Session):
    repository = DeploymentRepository(session)
    target = await repository.get(deployment_id)
    if target is None:
        raise HTTPException(404, "Deployment not found")
    try:
        deployment = await DeploymentService(session, GitService(), DockerService()).rollback(
            target, target.environment
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return deployment
