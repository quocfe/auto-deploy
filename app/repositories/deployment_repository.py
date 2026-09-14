from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Deployment, DeploymentLog, DeploymentStatus, DeploymentTrigger, Environment


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

    async def claim_next(self) -> Deployment | None:
        query = (
            select(Deployment)
            .options(selectinload(Deployment.environment).selectinload(Environment.project))
            .where(Deployment.status == DeploymentStatus.QUEUED)
            .order_by(Deployment.created_at, Deployment.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return await self.session.scalar(query)

    async def list_logs(self, deployment_id: int) -> list[DeploymentLog]:
        query = (
            select(DeploymentLog)
            .where(DeploymentLog.deployment_id == deployment_id)
            .order_by(DeploymentLog.id)
        )
        return list(await self.session.scalars(query))

    async def add_log(self, deployment: Deployment, level: str, message: str) -> DeploymentLog:
        log = DeploymentLog(deployment_id=deployment.id, level=level, message=message[:4000])
        self.session.add(log)
        await self.session.flush()
        return log
