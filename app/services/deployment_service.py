from __future__ import annotations

from datetime import UTC, datetime
from time import sleep

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Deployment, DeploymentStatus, DeploymentTrigger, Environment
from app.repositories.deployment_repository import DeploymentRepository
from app.services.docker_service import DockerService
from app.services.encryption_service import EncryptionService
from app.services.git_service import GitService


class DeploymentService:
    """Synchronous Phase-6 deployment orchestration, called outside a worker for now."""

    def __init__(
        self,
        session: AsyncSession,
        git: GitService,
        docker: DockerService,
        *,
        startup_wait_seconds: float | None = None,
    ):
        self.session = session
        self.git = git
        self.docker = docker
        self.startup_wait_seconds = (
            get_settings().container_startup_wait_seconds
            if startup_wait_seconds is None
            else startup_wait_seconds
        )

    @staticmethod
    def image_name(environment: Environment, commit_sha: str) -> str:
        project = environment.project
        return f"md-{project.slug}:{environment.name}-{commit_sha[:7]}"

    @staticmethod
    def environment_values(
        environment: Environment, encryption: EncryptionService | None = None
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for variable in environment.variables:
            result[variable.key] = (
                (encryption or EncryptionService()).decrypt(variable.value)
                if variable.is_secret
                else variable.value
            )
        return result

    async def queue_manual(self, environment: Environment) -> Deployment:
        project = environment.project
        if self.git.repository_exists(project.slug):
            self.git.fetch_repository(project.slug)
        else:
            self.git.clone_repository(project.slug, project.repository_url)
        commit_sha = self.git.get_remote_branch_sha(project.slug, environment.branch)
        commit_message = self.git.get_commit_message(project.slug, commit_sha)
        deployment = await DeploymentRepository(self.session).create(
            environment, commit_sha, commit_message, DeploymentTrigger.MANUAL
        )
        return deployment

    async def queue_webhook(
        self, environment: Environment, commit_sha: str, commit_message: str | None
    ) -> Deployment:
        return await DeploymentRepository(self.session).create(
            environment, commit_sha, commit_message or "", DeploymentTrigger.WEBHOOK
        )

    async def rollback(self, target: Deployment, environment: Environment) -> Deployment:
        if target.status != DeploymentStatus.SUCCESS or not target.image_name:
            raise ValueError("Rollback target must be a successful deployment with an image")
        if not self.docker.image_exists(target.image_name):
            raise ValueError("Rollback image is no longer available")
        deployment = Deployment(
            project_id=environment.project_id,
            environment_id=environment.id,
            commit_sha=target.commit_sha,
            commit_message=target.commit_message,
            image_name=target.image_name,
            container_name=environment.container_name,
            status=DeploymentStatus.QUEUED,
            trigger=DeploymentTrigger.ROLLBACK,
        )
        self.session.add(deployment)
        await self.session.flush()
        deployment.started_at = datetime.now(UTC)
        stage = DeploymentStatus.STOPPING_OLD
        try:
            await self._status(deployment, stage)
            self.docker.stop_container(environment.container_name)
            self.docker.remove_container(environment.container_name)
            stage = DeploymentStatus.STARTING
            await self._status(deployment, stage)
            self.docker.run_container(
                target.image_name,
                container_name=environment.container_name,
                environment=self.environment_values(environment),
                network=environment.docker_network,
            )
            sleep(self.startup_wait_seconds)
            stage = DeploymentStatus.VERIFYING
            await self._status(deployment, stage)
            if (
                not self.docker.inspect_container(environment.container_name)
                .get("State", {})
                .get("Running")
            ):
                raise RuntimeError("Container did not remain running")
            await self._status(deployment, DeploymentStatus.SUCCESS)
            await self.cleanup_images(environment)
        except Exception as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.failed_stage = stage.value
            await self._failure_logs(deployment, environment, exc)
        finally:
            deployment.finished_at = datetime.now(UTC)
            await self.session.flush()
        return deployment

    async def _status(self, deployment: Deployment, status: DeploymentStatus) -> None:
        deployment.status = status
        await self.session.flush()
        await DeploymentRepository(self.session).add_log(deployment, "INFO", status.value)

    def _redact(self, environment: Environment, message: str) -> str:
        redacted = message
        try:
            secrets = [
                EncryptionService().decrypt(variable.value)
                for variable in environment.variables
                if variable.is_secret
            ]
        except Exception:
            secrets = []
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted[:4000]

    async def _failure_logs(
        self, deployment: Deployment, environment: Environment, error: Exception
    ) -> None:
        repository = DeploymentRepository(self.session)
        deployment.error_message = self._redact(environment, str(error))
        await self.session.flush()
        await repository.add_log(deployment, "ERROR", deployment.error_message)
        if deployment.failed_stage in {
            DeploymentStatus.STARTING.value,
            DeploymentStatus.VERIFYING.value,
        }:
            try:
                output = self.docker.get_container_logs(environment.container_name)
            except Exception:
                return
            await repository.add_log(deployment, "ERROR", self._redact(environment, output))

    async def cleanup_images(self, environment: Environment, keep: int = 5) -> None:
        successful = await DeploymentRepository(self.session).latest_successful(environment.id)
        for deployment in successful[keep:]:
            if deployment.image_name:
                try:
                    self.docker.remove_image(deployment.image_name)
                    await DeploymentRepository(self.session).add_log(
                        deployment, "INFO", "Removed retained image"
                    )
                except Exception:
                    await DeploymentRepository(self.session).add_log(
                        deployment, "WARNING", "Could not remove retained image"
                    )

    async def execute(self, deployment: Deployment, environment: Environment) -> None:
        """Build before replacing a running container, preserving it on build failure."""
        project = environment.project
        deployment.started_at = datetime.now(UTC)
        stage = DeploymentStatus.PREPARING
        try:
            await self._status(deployment, stage)
            if self.git.repository_exists(project.slug):
                stage = DeploymentStatus.FETCHING
                await self._status(deployment, stage)
                self.git.fetch_repository(project.slug)
            else:
                self.git.clone_repository(project.slug, project.repository_url)
            stage = DeploymentStatus.CHECKING_OUT
            await self._status(deployment, stage)
            self.git.checkout_commit(project.slug, deployment.commit_sha)
            image_name = self.image_name(environment, deployment.commit_sha)
            deployment.image_name = image_name
            deployment.container_name = environment.container_name
            stage = DeploymentStatus.BUILDING
            await self._status(deployment, stage)
            build = self.docker.build_image(
                self.git.repository_path(project.slug),
                build_context=environment.build_context,
                dockerfile=environment.dockerfile,
                image_name=image_name,
            )
            for line in build.logs:
                await DeploymentRepository(self.session).add_log(deployment, "INFO", line)
            stage = DeploymentStatus.STOPPING_OLD
            await self._status(deployment, stage)
            self.docker.stop_container(environment.container_name)
            self.docker.remove_container(environment.container_name)
            stage = DeploymentStatus.STARTING
            await self._status(deployment, stage)
            self.docker.run_container(
                image_name,
                container_name=environment.container_name,
                environment=self.environment_values(environment),
                network=environment.docker_network,
            )
            sleep(self.startup_wait_seconds)
            stage = DeploymentStatus.VERIFYING
            await self._status(deployment, stage)
            state = self.docker.inspect_container(environment.container_name).get("State", {})
            if not state.get("Running"):
                raise RuntimeError("Container did not remain running")
            await self._status(deployment, DeploymentStatus.SUCCESS)
            await self.cleanup_images(environment)
        except Exception as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.failed_stage = stage.value
            await self._failure_logs(deployment, environment, exc)
        finally:
            deployment.finished_at = datetime.now(UTC)
            await self.session.flush()
