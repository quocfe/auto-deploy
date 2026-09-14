from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, DeploymentStatus, DeploymentTrigger, Environment
from app.repositories.deployment_repository import DeploymentRepository
from app.services.docker_service import DockerService
from app.services.git_service import GitService


class DeploymentService:
    """Synchronous Phase-6 deployment orchestration, called outside a worker for now."""

    def __init__(self, session: AsyncSession, git: GitService, docker: DockerService):
        self.session = session
        self.git = git
        self.docker = docker

    @staticmethod
    def image_name(environment: Environment, commit_sha: str) -> str:
        project = environment.project
        return f"md-{project.slug}:{environment.name}-{commit_sha[:7]}"

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

    async def _status(self, deployment: Deployment, status: DeploymentStatus) -> None:
        deployment.status = status
        await self.session.flush()

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
            self.docker.build_image(
                self.git.repository_path(project.slug),
                build_context=environment.build_context,
                dockerfile=environment.dockerfile,
                image_name=image_name,
            )
            stage = DeploymentStatus.STOPPING_OLD
            await self._status(deployment, stage)
            self.docker.stop_container(environment.container_name)
            self.docker.remove_container(environment.container_name)
            stage = DeploymentStatus.STARTING
            await self._status(deployment, stage)
            self.docker.run_container(
                image_name,
                container_name=environment.container_name,
                environment=None,
                network=environment.docker_network,
            )
            stage = DeploymentStatus.VERIFYING
            await self._status(deployment, stage)
            state = self.docker.inspect_container(environment.container_name).get("State", {})
            if not state.get("Running"):
                raise RuntimeError("Container did not remain running")
            await self._status(deployment, DeploymentStatus.SUCCESS)
        except Exception as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.failed_stage = stage.value
            deployment.error_message = str(exc)[:4000]
            await self.session.flush()
        finally:
            deployment.finished_at = datetime.now(UTC)
            await self.session.flush()
