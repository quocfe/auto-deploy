# Auto Deploy

A lightweight self-hosted deployment platform. Phase 1 provides a FastAPI application,
environment configuration, async SQLAlchemy infrastructure, Alembic, and PostgreSQL.
Phase 2 adds the core data model; Phase 3 adds Project and Environment management APIs.

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

## Management API (Phase 3)

After applying migrations, explore request and response schemas at `/docs`.

| Resource | Endpoints |
| --- | --- |
| Projects | `GET /api/projects`, `POST /api/projects` |
| Project | `GET`, `PATCH`, `DELETE /api/projects/{id}` |
| Project environments | `GET`, `POST /api/projects/{project_id}/environments` |
| Environment | `GET`, `PATCH`, `DELETE /api/environments/{id}` |

Create a project with `name`, `slug`, `repository_url`, and optional `provider`
(defaults to `github`). Create each environment under its project with `name`,
`branch`, `container_port`, and `container_name`. Docker defaults remain
`Dockerfile`, `.`, and `web_network`; automatic deployment defaults to false.

Project slugs and environment names use lowercase letters, digits, and single
hyphens between segments. Branches follow Git ref naming restrictions. Docker
names start with a letter or digit and use letters, digits, dots, underscores,
or hyphens. Ports must be integers from 1 to 65535. Build paths must be relative
without parent traversal. Domains are hostnames without schemes, paths, or ports.

POST returns 201; DELETE returns an empty 204 response. Missing resources return
404, uniqueness/relationship conflicts return 409, and invalid inputs return 422.
PATCH changes only supplied fields. `domain: null` clears a domain; other required
fields cannot be null. Unknown fields are rejected. Project ownership and
`latest_available_commit` cannot be changed through environment management requests.
Lists are ordered by ID. Database tests include CRUD, conflicts, and validation.

Management APIs have no authentication until Phase 15. Keep this development
instance bound to localhost. These endpoints only manage database records;
Git operations and container deployment arrive in later phases.
