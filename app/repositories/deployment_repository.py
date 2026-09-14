from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Deployment, DeploymentStatus, DeploymentTrigger, Environment


class DeploymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, deployment_id: int) -> Deployment | None:
        query = (
            select(Deployment)
            .options(selectinload(Deployment.environment).selectinload(Environment.project))
            .where(Deployment.id == deployment_id)
        )
        return await self.session.scalar(query)

    async def create(
        self,
        environment: Environment,
        commit_sha: str,
        commit_message: str,
        trigger: DeploymentTrigger,
    ) -> Deployment:
        deployment = Deployment(
            project_id=environment.project_id,
            environment_id=environment.id,
            commit_sha=commit_sha,
            commit_message=commit_message,
            status=DeploymentStatus.QUEUED,
            trigger=trigger,
        )
        self.session.add(deployment)
        await self.session.flush()
        return deployment
