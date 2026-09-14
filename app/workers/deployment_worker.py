from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.models import DeploymentStatus
from app.repositories.deployment_repository import DeploymentRepository
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerService
from app.services.git_service import GitService

logger = logging.getLogger(__name__)


async def process_next_deployment() -> bool:
    async with session_factory() as session:
        async with session.begin():
            deployment = await DeploymentRepository(session).claim_next()
            if deployment is None:
                return False
            deployment.status = DeploymentStatus.PREPARING
        # Commit the claim before invoking blocking Git or Docker work. This makes
        # the job invisible to other workers and exposes its progress to the API.
        await DeploymentService(session, GitService(), DockerService()).execute(
            deployment, deployment.environment
        )
        await session.commit()
        return True


async def run() -> None:
    settings = get_settings()
    while True:
        try:
            if not await process_next_deployment():
                await asyncio.sleep(settings.worker_poll_seconds)
        except Exception:
            logger.exception("Deployment worker iteration failed")
            await asyncio.sleep(settings.worker_poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
