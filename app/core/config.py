from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Auto Deploy"
    app_env: str = "development"
    repository_root: Path = Path("/opt/auto-deploy/repos")
    git_timeout_seconds: int = Field(default=300, ge=1)
    worker_poll_seconds: float = Field(default=2, gt=0, le=60)
    container_startup_wait_seconds: float = Field(default=3, ge=0, le=60)
    app_master_key: SecretStr | None = None
    session_secret: SecretStr = SecretStr("change-this-session-secret-before-production")
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None

    @model_validator(mode="after")
    def secure_production_settings(self) -> Self:
        if (
            self.app_env == "production"
            and self.session_secret.get_secret_value()
            == "change-this-session-secret-before-production"
        ):
            raise ValueError("SESSION_SECRET must be changed in production")
        return self

    database_host: str = "localhost"
    database_port: int = Field(default=5433, ge=1, le=65535)
    database_name: str = "auto_deploy"
    database_user: str = "auto_deploy"
    database_password: SecretStr = SecretStr("auto_deploy")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
