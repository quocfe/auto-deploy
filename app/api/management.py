from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.repositories.environment_repository import EnvironmentRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.management import (
    DeploymentRead,
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerService
from app.services.git_service import GitService, GitServiceError

router = APIRouter(prefix="/api")
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
