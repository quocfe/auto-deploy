from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.schemas.management import ProjectCreate, ProjectUpdate


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> list[Project]:
        return list(await self.session.scalars(select(Project).order_by(Project.id)))

    async def get(self, project_id: int) -> Project | None:
        return await self.session.get(Project, project_id)

    async def create(self, data: ProjectCreate) -> Project:
        project = Project(**data.model_dump())
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def update(self, project: Project, data: ProjectUpdate) -> Project:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(project, key, value)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def delete(self, project: Project) -> None:
        await self.session.delete(project)
        await self.session.flush()
