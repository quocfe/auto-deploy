import pytest

from app.models import Deployment, DeploymentStatus, DeploymentTrigger, Environment, Project
from app.services.deployment_service import DeploymentService
from app.services.docker_service import DockerServiceError


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        pass

    async def scalars(self, query):
        return []


class FakeGit:
    def __init__(self):
        self.calls = []

    def repository_exists(self, slug):
        self.calls.append(("exists", slug))
        return True

    def fetch_repository(self, slug):
        self.calls.append(("fetch", slug))

    def checkout_commit(self, slug, sha):
        self.calls.append(("checkout", slug, sha))

    def repository_path(self, slug):
        self.calls.append(("path", slug))
        return "/checkout"


class FakeDocker:
    def __init__(self, build_error=None):
        self.calls = []
        self.build_error = build_error

    def build_image(self, *args, **kwargs):
        self.calls.append(("build", args, kwargs))
        if self.build_error:
            raise self.build_error

    def image_exists(self, image):
        self.calls.append(("image_exists", image))
        return True

    def stop_container(self, name):
        self.calls.append(("stop", name))

    def remove_container(self, name):
        self.calls.append(("remove", name))

    def run_container(self, image, **kwargs):
        self.calls.append(("run", image, kwargs))

    def inspect_container(self, name):
        self.calls.append(("inspect", name))
        return {"State": {"Running": True}}


@pytest.fixture
def deployment():
    project = Project(
        id=1,
        name="App",
        slug="app",
        repository_url="https://example.com/app.git",
        provider="github",
    )
    environment = Environment(
        id=2,
        project_id=1,
        name="development",
        branch="main",
        container_port=8000,
        container_name="md-app-development",
        dockerfile="Dockerfile",
        build_context=".",
        docker_network="web_network",
    )
    environment.project = project
    return (
        Deployment(
            id=3,
            project_id=1,
            environment_id=2,
            commit_sha="a" * 40,
            trigger=DeploymentTrigger.MANUAL,
        ),
        environment,
    )


async def test_manual_deployment_builds_before_replacing_container(deployment):
    record, environment = deployment
    git = FakeGit()
    docker = FakeDocker()

    session = FakeSession()
    await DeploymentService(session, git, docker).execute(record, environment)

    assert record.status == DeploymentStatus.SUCCESS
    assert record.image_name == "md-app:development-aaaaaaa"
    assert [call[0] for call in docker.calls] == ["build", "stop", "remove", "run", "inspect"]
    assert docker.calls[0][2]["dockerfile"] == "Dockerfile"
    assert docker.calls[3][2]["network"] == "web_network"
    assert [log.level for log in session.added] == ["INFO"] * 8


async def test_build_failure_marks_deployment_failed_without_touching_old_container(deployment):
    record, environment = deployment
    docker = FakeDocker(DockerServiceError("Docker image build failed"))

    session = FakeSession()
    await DeploymentService(session, FakeGit(), docker).execute(record, environment)

    assert record.status == DeploymentStatus.FAILED
    assert record.failed_stage == "BUILDING"
    assert [call[0] for call in docker.calls] == ["build"]
    assert session.added[-1].level == "ERROR"


async def test_rollback_restarts_a_successful_deployment_image(deployment):
    _, environment = deployment
    target = Deployment(
        id=2,
        project_id=1,
        environment_id=environment.id,
        commit_sha="b" * 40,
        image_name="md-app:development-bbbbbbb",
        status=DeploymentStatus.SUCCESS,
        trigger=DeploymentTrigger.MANUAL,
    )
    session = FakeSession()
    docker = FakeDocker()

    rollback = await DeploymentService(session, FakeGit(), docker).rollback(target, environment)

    assert rollback.trigger == DeploymentTrigger.ROLLBACK
    assert rollback.status == DeploymentStatus.SUCCESS
    assert rollback.image_name == target.image_name
    assert [call[0] for call in docker.calls] == [
        "image_exists",
        "stop",
        "remove",
        "run",
        "inspect",
    ]
