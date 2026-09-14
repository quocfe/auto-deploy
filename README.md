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

Phase 1 intentionally has no domain tables or migration revisions. Phase 2 adds
the core models and their initial migration. Migrations are run explicitly.
Deployment execution, authentication, and the dashboard are planned for later phases.
