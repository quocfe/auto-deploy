import re
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Slug = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DockerName = Annotated[
    str, StringConstraints(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
]
Branch = Annotated[str, StringConstraints(min_length=1, max_length=255)]
RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchModel(InputModel):
    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        for name in self.model_fields_set:
            if name != "domain" and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ProjectCreate(InputModel):
    name: Name
    slug: Slug
    repository_url: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
    ]
    provider: Annotated[
        str, StringConstraints(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    ] = "github"


class ProjectUpdate(PatchModel):
    name: Name | None = None
    slug: Slug | None = None
    repository_url: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
        | None
    ) = None
    provider: (
        Annotated[
            str, StringConstraints(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
        ]
        | None
    ) = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    repository_url: str
    provider: str
    created_at: datetime
    updated_at: datetime


class EnvironmentValidation(InputModel):
    @field_validator("branch", check_fields=False)
    @classmethod
    def valid_branch(cls, value: str | None) -> str | None:
        if value is None:
            return value
        invalid_chars = any(
            ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[\\" for char in value
        )
        if (
            invalid_chars
            or value == "@"
            or value.startswith(("-", "/"))
            or value.endswith(("/", "."))
            or ".." in value
            or "@{" in value
            or any(
                not part or part.startswith(".") or part.endswith(".lock")
                for part in value.split("/")
            )
        ):
            raise ValueError("Invalid Git branch name")
        return value

    @field_validator("dockerfile", "build_context", check_fields=False)
    @classmethod
    def valid_path(cls, value: str | None) -> str | None:
        if value is not None and (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or ".." in value.split("/")
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("Use a relative repository path without parent traversal")
        return value

    @field_validator("domain", check_fields=False)
    @classmethod
    def valid_domain(cls, value: str | None) -> str | None:
        if value is not None and not all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in value.split(".")
        ):
            raise ValueError("Use a hostname without a scheme, path, or port")
        return value


class EnvironmentCreate(EnvironmentValidation):
    name: Slug
    branch: Branch
    dockerfile: RelativePath = "Dockerfile"
    build_context: RelativePath = "."
    container_port: Port
    container_name: DockerName
    docker_network: DockerName = "web_network"
    domain: Annotated[str, Field(max_length=253)] | None = None
    auto_deploy: bool = False
    enabled: bool = True


class EnvironmentUpdate(EnvironmentValidation, PatchModel):
    name: Slug | None = None
    branch: Branch | None = None
    dockerfile: RelativePath | None = None
    build_context: RelativePath | None = None
    container_port: Port | None = None
    container_name: DockerName | None = None
    docker_network: DockerName | None = None
    domain: Annotated[str, Field(max_length=253)] | None = None
    auto_deploy: bool | None = None
    enabled: bool | None = None


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    branch: str
    dockerfile: str
    build_context: str
    container_port: int
    container_name: str
    docker_network: str
    domain: str | None
    auto_deploy: bool
    enabled: bool
    latest_available_commit: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    environment_id: int
    commit_sha: str
    commit_message: str | None
    image_name: str | None
    container_name: str | None
    status: str
    trigger: str
    failed_stage: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
