from types import SimpleNamespace

import pytest
from docker.errors import DockerException, NotFound

from app.services.docker_service import DockerService, DockerServiceError


class FakeImages:
    def __init__(self):
        self.build_calls = []
        self.available = {"md-app:development-1234567"}

    def build(self, **kwargs):
        self.build_calls.append(kwargs)
        return "image", [{"stream": "Step 1/2 : FROM python\n"}, {"status": "Done"}]

    def get(self, name):
        if name not in self.available:
            raise NotFound("missing")
        return object()


class FakeContainer:
    def __init__(self):
        self.stop_calls = []
        self.remove_calls = []
        self.attrs = {"State": {"Running": True}}

    def stop(self, **kwargs):
        self.stop_calls.append(kwargs)

    def remove(self, **kwargs):
        self.remove_calls.append(kwargs)

    def logs(self, **kwargs):
        assert kwargs == {"stdout": True, "stderr": True}
        return b"application started\n"


class FakeContainers:
    def __init__(self):
        self.values = {}
        self.run_calls = []

    def get(self, name):
        if name not in self.values:
            raise NotFound("missing")
        return self.values[name]

    def run(self, image, **kwargs):
        self.run_calls.append((image, kwargs))
        container = FakeContainer()
        self.values[kwargs["name"]] = container
        return container


@pytest.fixture
def client():
    return SimpleNamespace(images=FakeImages(), containers=FakeContainers())


def test_build_image_only_uses_checked_out_repository_files(tmp_path, client):
    repository = tmp_path / "repository"
    (repository / "service").mkdir(parents=True)
    (repository / "service" / "Dockerfile").write_text("FROM scratch")
    service = DockerService(client)

    result = service.build_image(
        repository,
        build_context="service",
        dockerfile="service/Dockerfile",
        image_name="md-app:development-1234567",
    )

    assert result.image == "image"
    assert result.logs == ["Step 1/2 : FROM python", "Done"]
    assert client.images.build_calls == [
        {
            "path": str((repository / "service").resolve()),
            "dockerfile": "Dockerfile",
            "tag": "md-app:development-1234567",
            "rm": True,
            "pull": False,
        }
    ]


@pytest.mark.parametrize(
    ("build_context", "dockerfile"),
    [("../outside", "Dockerfile"), (".", "../Dockerfile"), (".", "/tmp/Dockerfile")],
)
def test_build_image_rejects_paths_outside_repository(tmp_path, client, build_context, dockerfile):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "Dockerfile").write_text("FROM scratch")

    with pytest.raises(ValueError):
        DockerService(client).build_image(
            repository,
            build_context=build_context,
            dockerfile=dockerfile,
            image_name="md-app:development-1234567",
        )
    assert client.images.build_calls == []


def test_run_container_uses_stable_name_network_and_no_host_ports(client):
    service = DockerService(client)

    container = service.run_container(
        "md-app:development-1234567",
        container_name="md-app-development",
        environment={"APP_ENV": "development"},
        network="web_network",
    )

    assert container is client.containers.values["md-app-development"]
    assert client.containers.run_calls == [
        (
            "md-app:development-1234567",
            {
                "name": "md-app-development",
                "environment": {"APP_ENV": "development"},
                "network": "web_network",
                "detach": True,
            },
        )
    ]


def test_container_lifecycle_inspection_and_logs(client):
    service = DockerService(client)
    container = service.run_container(
        "md-app:development-1234567",
        container_name="md-app-development",
        environment=None,
        network="web_network",
    )
    assert service.container_exists("md-app-development")
    assert service.image_exists("md-app:development-1234567")
    assert not service.image_exists("md-app:missing")
    assert service.inspect_container("md-app-development") == {"State": {"Running": True}}
    assert service.get_container_logs("md-app-development") == "application started\n"
    service.stop_container("md-app-development", timeout=3)
    service.remove_container("md-app-development")
    assert container.stop_calls == [{"timeout": 3}]
    assert container.remove_calls == [{"force": False}]
    assert not service.container_exists("not-present")


def test_missing_container_stop_and_remove_are_idempotent(client):
    service = DockerService(client)
    service.stop_container("not-present")
    service.remove_container("not-present")


def test_docker_errors_do_not_expose_daemon_details(tmp_path, client, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "Dockerfile").write_text("FROM scratch")

    def fail(**kwargs):
        raise DockerException("private registry token")

    monkeypatch.setattr(client.images, "build", fail)
    with pytest.raises(DockerServiceError) as error:
        DockerService(client).build_image(
            repository,
            build_context=".",
            dockerfile="Dockerfile",
            image_name="md-app:development-1234567",
        )
    assert "private registry token" not in str(error.value)
