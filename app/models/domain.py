from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DeploymentStatus(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    FETCHING = "FETCHING"
    CHECKING_OUT = "CHECKING_OUT"
    BUILDING = "BUILDING"
    STOPPING_OLD = "STOPPING_OLD"
    STARTING = "STARTING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DeploymentTrigger(StrEnum):
    MANUAL = "MANUAL"
    WEBHOOK = "WEBHOOK"
    ROLLBACK = "ROLLBACK"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    repository_url: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))

    environments: Mapped[list[Environment]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Environment(TimestampMixin, Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_environment_project_name"),
        UniqueConstraint("id", "project_id", name="uq_environment_id_project"),
        CheckConstraint("container_port BETWEEN 1 AND 65535", name="ck_environment_port"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str] = mapped_column(String(255))
    dockerfile: Mapped[str] = mapped_column(String(1024), server_default="Dockerfile")
    build_context: Mapped[str] = mapped_column(String(1024), server_default=".")
    container_port: Mapped[int] = mapped_column(Integer)
    container_name: Mapped[str] = mapped_column(String(255), unique=True)
    docker_network: Mapped[str] = mapped_column(String(255), server_default="web_network")
    domain: Mapped[str | None] = mapped_column(String(253))
    auto_deploy: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    latest_available_commit: Mapped[str | None] = mapped_column(String(64))

    project: Mapped[Project] = relationship(back_populates="environments")
    variables: Mapped[list[EnvironmentVariable]] = relationship(
        back_populates="environment", cascade="all, delete-orphan", passive_deletes=True
    )
    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="environment",
        foreign_keys="Deployment.environment_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EnvironmentVariable(TimestampMixin, Base):
    __tablename__ = "environment_variables"
    __table_args__ = (
        UniqueConstraint("environment_id", "key", name="uq_variable_environment_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    environment: Mapped[Environment] = relationship(back_populates="variables")


class Deployment(TimestampMixin, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_deployment_environment_project",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[int] = mapped_column(Integer, index=True)
    commit_sha: Mapped[str] = mapped_column(String(64))
    commit_message: Mapped[str | None] = mapped_column(Text)
    image_name: Mapped[str | None] = mapped_column(String(255))
    container_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus, name="deployment_status", validate_strings=True),
        server_default=DeploymentStatus.QUEUED.value,
        index=True,
    )
    trigger: Mapped[DeploymentTrigger] = mapped_column(
        Enum(DeploymentTrigger, name="deployment_trigger", validate_strings=True)
    )
    failed_stage: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="deployments", foreign_keys=[project_id])
    environment: Mapped[Environment] = relationship(
        back_populates="deployments", foreign_keys=[environment_id]
    )
    logs: Mapped[list[DeploymentLog]] = relationship(
        back_populates="deployment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DeploymentLog.id",
    )


class DeploymentLog(Base):
    __tablename__ = "deployment_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    deployment: Mapped[Deployment] = relationship(back_populates="logs")
