import os

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.models import (
    Deployment,
    DeploymentLog,
    DeploymentStatus,
    DeploymentTrigger,
    Environment,
    EnvironmentVariable,
    Project,
    User,
)


@pytest.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            yield session
        if transaction.is_active:
            await transaction.rollback()
    await engine.dispose()


def project(slug="garagehub"):
    return Project(
        name="GarageHub",
        slug=slug,
        repository_url="https://github.com/example/app.git",
        provider="github",
    )


def environment(owner, name="development"):
    return Environment(
        project=owner,
        name=name,
        branch="develop",
        container_port=8000,
        container_name=f"ad-{owner.slug}-{name}",
    )


async def graph(db):
    owner = project()
    env = environment(owner)
    deployment = Deployment(
        project=owner, environment=env, commit_sha="a" * 40, trigger=DeploymentTrigger.MANUAL
    )
    deployment.logs.append(DeploymentLog(level="INFO", message="Deployment queued"))
    env.variables.append(EnvironmentVariable(key="APP_ENV", value="development"))
    db.add(deployment)
    await db.flush()
    return owner, env, deployment


async def test_relationships_defaults_and_timestamps(db):
    owner, env, deployment = await graph(db)
    owner_id = owner.id
    db.expunge_all()
    loaded = await db.scalar(
        select(Project)
        .where(Project.id == owner_id)
        .options(
            selectinload(Project.environments).selectinload(Environment.variables),
            selectinload(Project.environments)
            .selectinload(Environment.deployments)
            .selectinload(Deployment.logs),
            selectinload(Project.deployments),
        )
    )
    env = loaded.environments[0]
    deployment = env.deployments[0]
    assert loaded.deployments[0] is deployment
    assert deployment.project is loaded
    assert deployment.environment is env
    assert env.variables[0].value == "development"
    assert env.variables[0].is_secret is False
    assert deployment.logs[0].message == "Deployment queued"
    assert (env.dockerfile, env.build_context, env.docker_network) == (
        "Dockerfile",
        ".",
        "web_network",
    )
    assert env.enabled is True
    assert env.auto_deploy is False
    assert deployment.status is DeploymentStatus.QUEUED
    assert deployment.started_at is None
    assert deployment.finished_at is None
    assert env.created_at.tzinfo is not None
    assert env.updated_at is not None


@pytest.mark.parametrize("status", list(DeploymentStatus))
async def test_status_enum_persistence(db, status):
    _, _, deployment = await graph(db)
    deployment.status = status
    await db.flush()
    deployment_id = deployment.id
    db.expunge_all()
    loaded = await db.get(Deployment, deployment_id)
    assert loaded.status is status
    assert (
        await db.scalar(
            text("SELECT status::text FROM deployments WHERE id=:id"), {"id": deployment_id}
        )
        == status.value
    )


@pytest.mark.parametrize("trigger", list(DeploymentTrigger))
async def test_trigger_enum_persistence(db, trigger):
    _, _, deployment = await graph(db)
    deployment.trigger = trigger
    await db.flush()
    deployment_id = deployment.id
    db.expunge_all()
    assert (await db.get(Deployment, deployment_id)).trigger is trigger


async def test_unique_project_slug(db):
    db.add_all([project(), project()])
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.parametrize("field", ["name", "slug", "repository_url", "provider"])
async def test_required_project_fields(db, field):
    owner = project()
    setattr(owner, field, None)
    db.add(owner)
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_user_persistence_and_unique_username(db):
    user = User(username="admin", password_hash="hashed-password")
    db.add(user)
    await db.flush()
    user_id = user.id
    db.expunge_all()
    assert (await db.get(User, user_id)).password_hash == "hashed-password"
    db.add(User(username="admin", password_hash="another-hash"))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.parametrize("port", [0, 65536])
async def test_invalid_container_port(db, port):
    env = environment(project())
    env.container_port = port
    db.add(env)
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_unique_environment_name_per_project(db):
    owner, _, _ = await graph(db)
    env = environment(owner)
    env.container_name = "another-container"
    db.add(env)
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_same_environment_name_allowed_for_other_project(db):
    db.add_all([environment(project("first")), environment(project("second"))])
    await db.flush()


async def test_unique_variable_key_per_environment(db):
    _, env, _ = await graph(db)
    db.add(EnvironmentVariable(environment=env, key="APP_ENV", value="duplicate"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_deployment_cannot_reference_another_projects_environment(db):
    _, env, _ = await graph(db)
    other = project("other")
    db.add(other)
    await db.flush()
    db.add(
        Deployment(
            project_id=other.id,
            environment_id=env.id,
            commit_sha="b" * 40,
            trigger=DeploymentTrigger.MANUAL,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_database_delete_cascades(db):
    owner, _, _ = await graph(db)
    await db.execute(delete(Project).where(Project.id == owner.id))
    for model in [Project, Environment, EnvironmentVariable, Deployment, DeploymentLog]:
        assert await db.scalar(select(func.count()).select_from(model)) == 0
