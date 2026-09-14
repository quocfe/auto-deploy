from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnvironmentVariable
from app.schemas.management import EnvironmentVariableCreate, EnvironmentVariableUpdate
from app.services.encryption_service import EncryptionService


class EnvironmentVariableRepository:
    def __init__(self, session: AsyncSession, encryption: EncryptionService | None = None):
        self.session = session
        self.encryption = encryption

    def _encryption(self) -> EncryptionService:
        if self.encryption is None:
            self.encryption = EncryptionService()
        return self.encryption

    async def list(self, environment_id: int) -> list[EnvironmentVariable]:
        query = (
            select(EnvironmentVariable)
            .where(EnvironmentVariable.environment_id == environment_id)
            .order_by(EnvironmentVariable.id)
        )
        return list(await self.session.scalars(query))

    async def get(self, variable_id: int) -> EnvironmentVariable | None:
        return await self.session.get(EnvironmentVariable, variable_id)

    async def create(
        self, environment_id: int, data: EnvironmentVariableCreate
    ) -> EnvironmentVariable:
        value = self._encryption().encrypt(data.value) if data.is_secret else data.value
        variable = EnvironmentVariable(
            environment_id=environment_id, key=data.key, value=value, is_secret=data.is_secret
        )
        self.session.add(variable)
        await self.session.flush()
        await self.session.refresh(variable)
        return variable

    async def update(
        self, variable: EnvironmentVariable, data: EnvironmentVariableUpdate
    ) -> EnvironmentVariable:
        changes = data.model_dump(exclude_unset=True)
        target_secret = changes.get("is_secret", variable.is_secret)
        if "value" in changes:
            value = changes["value"]
            variable.value = self._encryption().encrypt(value) if target_secret else value
        elif target_secret != variable.is_secret:
            plaintext = (
                self._encryption().decrypt(variable.value) if variable.is_secret else variable.value
            )
            variable.value = self._encryption().encrypt(plaintext) if target_secret else plaintext
        variable.is_secret = target_secret
        if "key" in changes:
            variable.key = changes["key"]
        await self.session.flush()
        await self.session.refresh(variable)
        return variable

    async def delete(self, variable: EnvironmentVariable) -> None:
        await self.session.delete(variable)
        await self.session.flush()
