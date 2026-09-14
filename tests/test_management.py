import os

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import get_session
from app.main import app
from app.schemas.management import (
    EnvironmentCreate,
    EnvironmentUpdate,
    ProjectCreate,
    ProjectUpdate,
)


@pytest.fixture
async def client():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()

            async def override_session():
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    yield session

            previous = app.dependency_overrides.copy()
            app.dependency_overrides[get_session] = override_session
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as http:
                    yield http
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous)
                await transaction.rollback()
    finally:
        await engine.dispose()


def project_data(slug="garagehub"):
    return {
        "name": "GarageHub",
        "slug": slug,
        "repository_url": "https://github.com/example/app.git",
        "provider": "github",
    }


def environment_data(name="development", branch="develop"):
    return {
        "name": name,
        "branch": branch,
        "container_port": 8000,
        "container_name": f"ad-garagehub-{name}",
    }


async def create_project(client, slug="garagehub"):
    response = await client.post("/api/projects", json=project_data(slug))
    assert response.status_code == 201, response.text
    return response.json()


async def create_environment(client, project_id, name="development", branch="develop"):
    response = await client.post(
        f"/api/projects/{project_id}/environments", json=environment_data(name, branch)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_project_crud(client):
    assert (await client.get("/api/projects")).json() == []
    project = await create_project(client)
    url = f"/api/projects/{project['id']}"
    assert (await client.get(url)).json() == project
    assert (await client.get("/api/projects")).json() == [project]
    updated = await client.patch(url, json={"name": "Updated"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated"
    assert updated.json()["slug"] == project["slug"]
    assert (await client.get(url)).json()["name"] == "Updated"
    deleted = await client.delete(url)
    assert deleted.status_code == 204 and deleted.content == b""
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/projects")).json() == []


async def test_duplicate_project_create_and_update_are_atomic(client):
    first = await create_project(client)
    second = await create_project(client, "second")
    response = await client.post("/api/projects", json=project_data())
    assert response.status_code == 409
    url = f"/api/projects/{second['id']}"
    response = await client.patch(url, json={"slug": first["slug"], "name": "Must not persist"})
    assert response.status_code == 409
    assert (await client.get(url)).json() == second
    assert (await client.patch(url, json={"name": "Valid update"})).status_code == 200


async def test_environment_crud_and_project_scoping(client):
    project = await create_project(client)
    other = await create_project(client, "other")
    listing = f"/api/projects/{project['id']}/environments"
    assert (await client.get(listing)).json() == []
    environments = [
        await create_environment(client, project["id"], name, branch)
        for name, branch in [
            ("development", "develop"),
            ("staging", "staging"),
            ("production", "main"),
        ]
    ]
    assert (await client.get(listing)).json() == environments
    assert (await client.get(f"/api/projects/{other['id']}/environments")).json() == []
    env = environments[0]
    assert env["dockerfile"] == "Dockerfile" and env["build_context"] == "."
    assert env["docker_network"] == "web_network" and env["auto_deploy"] is False
    url = f"/api/environments/{env['id']}"
    assert (await client.get(url)).json() == env
    patch = {
        "branch": "feature/api",
        "domain": "dev.example.com",
        "auto_deploy": True,
        "enabled": False,
        "container_port": 8080,
        "docker_network": "custom_net",
    }
    response = await client.patch(url, json=patch)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in patch.items())
    assert response.json()["container_name"] == env["container_name"]
    assert (await client.patch(url, json={"domain": None})).json()["domain"] is None
    deleted = await client.delete(url)
    assert deleted.status_code == 204 and deleted.content == b""
    assert (await client.get(url)).status_code == 404
    assert len((await client.get(listing)).json()) == 2


async def test_project_delete_cascades_environments(client):
    project = await create_project(client)
    env = await create_environment(client, project["id"])
    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 204
    assert (await client.get(f"/api/environments/{env['id']}")).status_code == 404


async def test_environment_conflicts_and_recovery(client):
    project = await create_project(client)
    first = await create_environment(client, project["id"])
    second = await create_environment(client, project["id"], "staging", "staging")
    listing = f"/api/projects/{project['id']}/environments"
    duplicate_name = environment_data()
    duplicate_name["container_name"] = "different-container"
    assert (await client.post(listing, json=duplicate_name)).status_code == 409
    duplicate_container = environment_data("production", "main")
    duplicate_container["container_name"] = first["container_name"]
    assert (await client.post(listing, json=duplicate_container)).status_code == 409
    url = f"/api/environments/{second['id']}"
    assert (await client.patch(url, json={"name": first["name"]})).status_code == 409
    assert (await client.get(url)).json() == second
    assert (await client.patch(url, json={"enabled": False})).status_code == 200


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/projects/999999", None),
        ("PATCH", "/api/projects/999999", {"name": "Updated"}),
        ("DELETE", "/api/projects/999999", None),
        ("GET", "/api/projects/999999/environments", None),
        ("POST", "/api/projects/999999/environments", environment_data()),
        ("GET", "/api/environments/999999", None),
        ("PATCH", "/api/environments/999999", {"branch": "main"}),
        ("DELETE", "/api/environments/999999", None),
    ],
)
async def test_missing_resources(client, method, path, body):
    assert (await client.request(method, path, json=body)).status_code == 404


async def test_invalid_http_payloads_and_patch_ownership(client):
    assert (
        await client.post("/api/projects", json={**project_data(), "slug": "../bad"})
    ).status_code == 422
    project = await create_project(client)
    env = await create_environment(client, project["id"])
    project_url = f"/api/projects/{project['id']}"
    env_url = f"/api/environments/{env['id']}"
    assert (await client.patch(project_url, json={"name": None})).status_code == 422
    for payload in [
        {"branch": None},
        {"container_port": 0},
        {"project_id": 123},
        {"latest_available_commit": "a" * 40},
        {"id": 123},
    ]:
        assert (await client.patch(env_url, json=payload)).status_code == 422
    assert (await client.patch(env_url, json={})).json() == env


@pytest.mark.parametrize(
    "slug", ["", "../bad", "Uppercase", "with space", "-leading", "trailing-", "a/b"]
)
def test_slug_validation(slug):
    with pytest.raises(ValidationError):
        ProjectCreate(**{**project_data(), "slug": slug})
    with pytest.raises(ValidationError):
        ProjectUpdate(slug=slug)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "../development"),
        ("name", "with space"),
        ("name", ""),
        ("branch", "-main"),
        ("branch", "a..b"),
        ("branch", "a b"),
        ("branch", "@"),
        ("branch", "a@{b"),
        ("branch", "feature/.hidden"),
        ("branch", "feature/a.lock"),
        ("branch", "a//b"),
        ("branch", "a/"),
        ("branch", "a\\b"),
        ("branch", "a~b"),
        ("container_name", "-invalid"),
        ("container_name", "bad/name"),
        ("docker_network", "../network"),
        ("docker_network", ""),
        ("container_port", 0),
        ("container_port", 65536),
        ("container_port", True),
        ("dockerfile", "../Dockerfile"),
        ("build_context", "/tmp"),
        ("build_context", "a/../b"),
        ("domain", "https://example.com"),
        ("domain", "bad..example.com"),
    ],
)
def test_environment_validation_on_create_and_update(field, value):
    with pytest.raises(ValidationError):
        EnvironmentCreate(**{**environment_data(), field: value})
    with pytest.raises(ValidationError):
        EnvironmentUpdate(**{field: value})


@pytest.mark.parametrize("branch", ["main", "feature/new-api", "release/v1.2", "staging"])
def test_valid_branches(branch):
    assert EnvironmentCreate(**environment_data(branch=branch)).branch == branch
