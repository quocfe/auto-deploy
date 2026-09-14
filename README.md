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
Environment variables are scoped to one environment. Secret variables are encrypted at
rest with Fernet using `APP_MASTER_KEY`, then decrypted only when the worker starts
the container. Generate and set a persistent key before adding secrets:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Variable API responses never return their values, preventing the management API from
accidentally exposing either plaintext or encrypted secret material.
Timestamps are timezone-aware; `updated_at` is maintained by SQLAlchemy updates.
Deployment execution runs in the worker; the dashboard and management API require
an authenticated session.

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

Management APIs require an authenticated session. Keep this development instance
bound to localhost. Deployment requests enqueue work for the separate worker.

## Git service (Phase 4)

`app.services.git_service.GitService` stores one checkout per project under
`REPOSITORY_ROOT` (default `/opt/auto-deploy/repos`). `GIT_TIMEOUT_SECONDS` defaults
to 300 per command. The Docker image includes Git and SSH and gives the application
user ownership of the default storage directory. Custom roots must be writable.
Compose shares persistent repository storage between the API and worker services.

The service supports `repository_exists`, `clone_repository`, `fetch_repository`,
`checkout_commit`, `get_commit_message`, and `get_remote_branch_sha`. Each method
takes a project slug; clone additionally takes a repository URL, commit methods take
a full 40- or 64-character SHA, and branch lookup takes a branch name.

Cloning creates a checkout without populating its worktree. Fetch runs
`git fetch --all --prune`. Checkout verifies a commit object before performing
a detached checkout, `git reset --hard <sha>`, and `git clean -fd`. These operations
discard tracked changes and untracked files in that project's checkout; Git-ignored
files remain. Existing clone destinations are rejected without overwriting them.
Branch lookup queries origin and returns the current remote SHA; fetch must run
before checking out an object that has not been downloaded yet.

Use HTTPS or SSH URLs without embedded passwords. Local repositories are disabled
unless explicitly enabled with `allow_local_repositories=True` for tests. Git runs
without a shell, interactive prompts, inherited Git-specific environment variables,
global Git configuration, or hooks. SSH requires provisioned credentials and trusted
host keys; the service will not prompt to accept a new host. Errors deliberately omit
Git output and command arguments because they can contain credentials.

Git tests use temporary local repositories and require Git on the test PATH.
The service is synchronous and does not serialize concurrent access to a project;
worker coordination arrives in Phase 7.

## Manual deployment (Phase 6)

`POST /api/environments/{id}/deploy` resolves the environment branch to an exact
remote SHA, creates a queued deployment record, and returns HTTP 202. The worker
claims it with PostgreSQL `FOR UPDATE SKIP LOCKED`, then fetches or clones the
project checkout, checks out that SHA, builds
`md-{project-slug}:{environment}-{short-sha}`, then replaces the stable environment
container on its configured Docker network. Application ports are never published
to the host, so Nginx Proxy Manager can keep routing by the stable container name.

The build always completes before an existing container is stopped; a build failure
therefore leaves the running version untouched. The API and worker share the
repository volume; only the worker performs the blocking Docker operations.

## GitHub webhooks (Phases 11–12)

Set `GITHUB_WEBHOOK_SECRET`, then configure GitHub push events to
`/webhooks/github/{project_id}`. Each request is verified with
`X-Hub-Signature-256`. A matching environment records the pushed SHA in
`latest_available_commit`; it is queued only when both `enabled` and `auto_deploy`
are true. The dashboard shows a pending SHA and deploy control for production
environments with auto-deploy disabled.

## Production security

Set a unique `SESSION_SECRET`, `APP_MASTER_KEY`, `ADMIN_USERNAME`,
`ADMIN_PASSWORD`, and `GITHUB_WEBHOOK_SECRET` before production use. The app refuses
the default session secret in production. Dashboard and management routes require a
logged-in session; GitHub webhooks are instead authenticated by their HMAC signature.
