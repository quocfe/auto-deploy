# Auto Deploy

A lightweight self-hosted deployment platform. Phase 1 provides a FastAPI application,
environment configuration, async SQLAlchemy infrastructure, Alembic, and PostgreSQL.

## Run with Docker

Requires Docker Engine with the Compose plugin.

```sh
docker compose up -d --build
curl http://localhost:8000/health
```

The response is HTTP 200 with `{"status":"ok"}`. API documentation is at
http://localhost:8000/docs. The health endpoint reports application liveness;
it does not query the database. Compose waits for PostgreSQL health before starting the API.

Optional: copy `.env.example` to `.env` to override defaults. Compose sets the API's
database hostname to `postgres` and internal port to `5432`; `DATABASE_PORT` controls
the host port used by local Python development. Both published ports bind to localhost.
The example credentials are for development; set your own password before server use.
PostgreSQL credentials initialize a new data volume only; changing `.env` does not
change an existing database password.

```sh
docker compose logs -f api
docker compose down
```

Database data persists in the `postgres_data` volume when services stop.

## Local development

Requires Python 3.12 or newer. From the repository root:

```sh
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env`, then:

```sh
docker compose up -d postgres
uvicorn app.main:app --reload
```

Settings load from environment variables and `.env`; environment variables take precedence.
`DATABASE_HOST` defaults to `localhost` for local development. Database connections are
opened only when used, so health endpoint tests do not require PostgreSQL.

## Checks

```sh
pytest
ruff check .
ruff format --check .
```

## Migrations

Alembic uses the same environment configuration as the API:

```sh
alembic upgrade head
# Or inside Compose:
docker compose exec api alembic upgrade head
```

Phase 2 adds User, Project, Environment, EnvironmentVariable, Deployment, and
DeploymentLog with PostgreSQL status/trigger enums. Migrations are run explicitly.
Environment names are unique per project; container names are globally unique.
Deleting a project or environment cascades to its deployment history and variables.
Secrets are not encrypted yet; the variable model is storage infrastructure for Phase 9.
Timestamps are timezone-aware; `updated_at` is maintained by SQLAlchemy updates.
Deployment execution, authentication, and the dashboard are planned for later phases.

Database tests require a separate disposable PostgreSQL database. Set the `DATABASE_*`
variables to that database, run `alembic upgrade head`, and set `TEST_DATABASE_URL`
to its `postgresql+asyncpg://user:password@host:port/database` URL before running
`pytest`. Tests exercise the migrated schema and roll back their changes. Without
`TEST_DATABASE_URL`, database tests are skipped and health/configuration tests still run.
Use `alembic check` to detect schema drift. On a disposable database, verify migration
reversibility with `alembic downgrade base` followed by `alembic upgrade head`.
