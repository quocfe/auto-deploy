from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Environment
from app.schemas.management import EnvironmentCreate, EnvironmentUpdate


class EnvironmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, project_id: int) -> list[Environment]:
        query = (
            select(Environment).where(Environment.project_id == project_id).order_by(Environment.id)
        )
        return list(await self.session.scalars(query))

    async def list_by_branch(self, project_id: int, branch: str) -> list[Environment]:
        query = (
            select(Environment)
            .where(Environment.project_id == project_id, Environment.branch == branch)
            .order_by(Environment.id)
        )
        return list(await self.session.scalars(query))

    async def get(self, environment_id: int) -> Environment | None:
        return await self.session.get(Environment, environment_id)

    async def create(self, project_id: int, data: EnvironmentCreate) -> Environment:
        environment = Environment(project_id=project_id, **data.model_dump())
        self.session.add(environment)
        await self.session.flush()
        await self.session.refresh(environment)
        return environment

    async def update(self, environment: Environment, data: EnvironmentUpdate) -> Environment:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(environment, key, value)
        await self.session.flush()
        await self.session.refresh(environment)
        return environment

    async def delete(self, environment: Environment) -> None:
        await self.session.delete(environment)
        await self.session.flush()
