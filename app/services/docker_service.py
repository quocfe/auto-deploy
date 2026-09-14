"""A small, validated wrapper around the Docker SDK.

Deployment orchestration belongs in :mod:`app.services.deployment_service`; this
module deliberately exposes only the Docker operations that orchestration needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound


class DockerServiceError(RuntimeError):
    """A Docker operation failed without exposing potentially sensitive details."""


@dataclass(frozen=True)
class DockerBuildResult:
    image: Any
    logs: list[str]


class DockerService:
    """Perform safe, fixed-shape Docker operations for a deployment."""

    _docker_name = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}")

    def __init__(self, client: Any | None = None):
        self.client = client if client is not None else docker.from_env()

    @classmethod
    def _name(cls, value: str, field: str) -> str:
        if not isinstance(value, str) or not cls._docker_name.fullmatch(value):
            raise ValueError(f"Invalid {field}")
        return value

    @staticmethod
    def _repository_path(repository_path: Path) -> Path:
        path = Path(repository_path).resolve()
        if not path.is_dir():
            raise ValueError("Repository path must be an existing directory")
        return path

    @staticmethod
    def _relative_path(value: str, field: str) -> Path:
        path = Path(value)
        if (
            not value
            or path.is_absolute()
            or "\\" in value
            or ".." in path.parts
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError(f"Invalid {field}")
        return path

    @staticmethod
    def _within(root: Path, candidate: Path, field: str) -> Path:
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{field} must stay inside the repository")
        return resolved

    @staticmethod
    def _environment(values: dict[str, str] | None) -> dict[str, str]:
        if values is None:
            return {}
        if not isinstance(values, dict):
            raise ValueError("Container environment must be a mapping")
        result: dict[str, str] = {}
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\x00" in key
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ValueError("Invalid container environment")
            result[key] = value
        return result

    @staticmethod
    def _build_logs(events: list[dict[str, Any]]) -> list[str]:
        logs: list[str] = []
        for event in events:
            message = event.get("stream") or event.get("status") or event.get("error")
            if isinstance(message, str):
                logs.extend(line for line in message.splitlines() if line)
        return logs

    def build_image(
        self,
        repository_path: Path,
        *,
        build_context: str,
        dockerfile: str,
        image_name: str,
    ) -> DockerBuildResult:
        """Build and tag an image from a validated directory within a checkout."""
        root = self._repository_path(repository_path)
        context_relative = self._relative_path(build_context, "build context")
        dockerfile_relative = self._relative_path(dockerfile, "Dockerfile path")
        context = self._within(root, root / context_relative, "Build context")
        dockerfile_path = self._within(root, root / dockerfile_relative, "Dockerfile path")
        if not context.is_dir():
            raise ValueError("Build context must be a directory")
        if not dockerfile_path.is_file():
            raise ValueError("Dockerfile must be an existing file")
        try:
            dockerfile_argument = str(dockerfile_path.relative_to(context))
        except ValueError:
            raise ValueError("Dockerfile must be inside the build context") from None
        if image_name.startswith("-") or any(ord(char) <= 32 for char in image_name):
            raise ValueError("Invalid image name")
        try:
            image, logs = self.client.images.build(
                path=str(context),
                dockerfile=dockerfile_argument,
                tag=image_name,
                rm=True,
                pull=False,
            )
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker image build failed") from None
        return DockerBuildResult(image=image, logs=self._build_logs(logs))

    def run_container(
        self,
        image_name: str,
        *,
        container_name: str,
        environment: dict[str, str] | None,
        network: str,
    ) -> Any:
        """Start a detached application container without publishing host ports."""
        self._name(container_name, "container name")
        self._name(network, "Docker network")
        if (
            not image_name
            or image_name.startswith("-")
            or any(ord(char) <= 32 for char in image_name)
        ):
            raise ValueError("Invalid image name")
        try:
            return self.client.containers.run(
                image_name,
                name=container_name,
                environment=self._environment(environment),
                network=network,
                detach=True,
            )
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container start failed") from None

    def stop_container(self, container_name: str, *, timeout: int = 10) -> None:
        self._name(container_name, "container name")
        if not isinstance(timeout, int) or timeout < 0:
            raise ValueError("Invalid stop timeout")
        try:
            self.client.containers.get(container_name).stop(timeout=timeout)
        except NotFound:
            return
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container stop failed") from None

    def remove_container(self, container_name: str, *, force: bool = False) -> None:
        self._name(container_name, "container name")
        try:
            self.client.containers.get(container_name).remove(force=force)
        except NotFound:
            return
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container removal failed") from None

    def container_exists(self, container_name: str) -> bool:
        self._name(container_name, "container name")
        try:
            self.client.containers.get(container_name)
        except NotFound:
            return False
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container lookup failed") from None
        return True

    def image_exists(self, image_name: str) -> bool:
        if (
            not image_name
            or image_name.startswith("-")
            or any(ord(char) <= 32 for char in image_name)
        ):
            raise ValueError("Invalid image name")
        try:
            self.client.images.get(image_name)
        except (ImageNotFound, NotFound):
            return False
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker image lookup failed") from None
        return True

    def get_container_logs(self, container_name: str) -> str:
        self._name(container_name, "container name")
        try:
            output = self.client.containers.get(container_name).logs(stdout=True, stderr=True)
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container log retrieval failed") from None
        return (
            output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        )

    def inspect_container(self, container_name: str) -> dict[str, Any]:
        self._name(container_name, "container name")
        try:
            details = self.client.containers.get(container_name).attrs
        except (APIError, DockerException, OSError):
            raise DockerServiceError("Docker container inspection failed") from None
        if not isinstance(details, dict):
            raise DockerServiceError("Docker returned invalid container details")
        return details
