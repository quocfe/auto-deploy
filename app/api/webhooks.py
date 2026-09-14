from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.repositories.environment_repository import EnvironmentRepository
from app.repositories.project_repository import ProjectRepository
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerService
from app.services.git_service import GitService
from app.services.webhook_service import (
    WebhookError,
    push_branch,
    push_commit,
    verify_github_signature,
)

router = APIRouter(prefix="/webhooks")
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/github/{project_id}", status_code=202)
async def github_push(
    project_id: int,
    request: Request,
    session: Session,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
):
    payload_bytes = await request.body()
    settings = get_settings()
    try:
        verify_github_signature(
            payload_bytes,
            x_hub_signature_256,
            settings.github_webhook_secret.get_secret_value()
            if settings.github_webhook_secret
            else None,
        )
        payload = await request.json()
        branch = push_branch(payload)
        commit_sha, commit_message = push_commit(payload)
    except WebhookError as exc:
        raise HTTPException(401, str(exc)) from exc
    if await ProjectRepository(session).get(project_id) is None:
        raise HTTPException(404, "Project not found")
    environments = await EnvironmentRepository(session).list_by_branch(project_id, branch)
    queued = 0
    for environment in environments:
        environment.latest_available_commit = commit_sha
        if environment.enabled and environment.auto_deploy:
            await DeploymentService(session, GitService(), DockerService()).queue_webhook(
                environment, commit_sha, commit_message
            )
            queued += 1
    await session.commit()
    return {"matched_environments": len(environments), "queued_deployments": queued}
