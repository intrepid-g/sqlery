# Technology Stack

**Analysis Date:** 2026-05-13

## Languages

**Primary:**
- Python 3.10+ — all library source, tests, tooling (`src/sqlery/`, `tests/`)
- Uses modern union syntax (`str | None`) — Python 3.10 minimum is enforced via `requires-python = ">=3.10"` in `pyproject.toml`

**Secondary:**
- Jinja2 / HTML — dashboard templates (`src/sqlery/fastapi_sqlery/templates/`, `src/sqlery/django_sqlery/templates/`)
- CSS / JavaScript — dashboard static assets (`src/sqlery/django_sqlery/static/`)
- SQL — Alembic migration scripts (`alembic/`) and Django migrations (`src/sqlery/django_sqlery/migrations/`)

## Runtime

**Environment:**
- CPython 3.10+ (minimum)
- Classifiers in `pyproject.toml` advertise support through 3.14
- CI matrix (see `.github/workflows/test.yml`) targets 3.11–3.13

**Package Manager:**
- `uv` (primary, used in CI and `Makefile`)
- pip-compatible — standard `pyproject.toml` with `hatchling` build backend
- Lockfile: `uv.lock` present at repo root

## Frameworks

**Core (optional integration modes):**
- Django >= 4.2 — Django integration mode (ORM, admin, management commands). Classifiers list 4.2, 5.0, 6.0.
- FastAPI >= 0.104.0 — Standalone-mode web dashboard and REST API (`src/sqlery/fastapi_sqlery/app.py`)
- SQLModel >= 0.0.14 — Standalone ORM layer (SQLAlchemy + Pydantic) (`src/sqlery/core/models.py`)
- SQLAlchemy (transitive via SQLModel) — engine, session management, connection pooling (`src/sqlery/fastapi_sqlery/database.py`)
- Uvicorn[standard] >= 0.24.0 — ASGI server for the standalone dashboard

**Testing:**
- pytest >= 7.4.0 — test runner (config in `pyproject.toml` `[tool.pytest.ini_options]`)
- pytest-django >= 4.5.0 — Django integration tests; `DJANGO_SETTINGS_MODULE = "tests.settings"`
- pytest-asyncio >= 0.23.0 — async test support
- pytest-cov >= 4.1.0 — coverage reporting
- pytest-timeout >= 2.2.0 — per-test timeout enforcement
- hypothesis >= 6.92.0 — property-based and chaos testing

**Build / Dev Tooling:**
- hatchling — build backend; wheel packages `src/sqlery` (`[tool.hatch.build.targets.wheel]`)
- black >= 23.0.0 — formatter, line-length 100, target py310
- ruff >= 0.1.0 — linter, line-length 100, target py310
- uv — virtualenv + dependency resolution
- GNU Make — `Makefile` targets for sample project, workers, stress tests

## Key Dependencies

**Always required (core):**
- `croniter` >= 2.0.0 — cron expression parsing / next-occurrence
- `uuid6` >= 2024.1.0 — UUID v7 generation for time-sortable worker IDs

**Optional extras (declared in `[project.optional-dependencies]`):**
- `postgres`: `psycopg` >= 3.1 — PostgreSQL adapter (psycopg3, async-capable)
- `cli`: `typer` >= 0.9.0, `rich` >= 13.0.0 — Typer CLI + Rich terminal formatting
- `django`: `django` >= 4.2
- `standalone`: `sqlmodel` >= 0.0.14, `fastapi` >= 0.104.0, `uvicorn[standard]` >= 0.24.0, `jinja2` >= 3.1.0, `typer` >= 0.9.0, `rich` >= 13.0.0, `alembic` >= 1.12.0
- `tasks`: `django-tasks` >= 0.1.0 — optional async execution backend
- `http`: `httpx` >= 0.24.0 — async HTTP client for HTTP-trigger mode
- `eventbridge`: `boto3` >= 1.34.0 — AWS EventBridge/Lambda integration
- Bundle extras: `all-django`, `all-standalone`, `all`

**Not declared in pyproject (imported with try/except fallback):**
- `requests` — used in `src/sqlery/webhooks.py` for synchronous webhook delivery; guarded `try: import requests except ImportError: requests = None`
- `boto3` — also guarded in `src/sqlery/eventbridge_trigger.py`

## Configuration

**Django mode:**
- Single `DJANGO_SQL_JOBS` dict in `settings.py`
- Defaults defined in `src/sqlery/django_sqlery/settings.py` (`DEFAULTS` dict)
- Runtime access via `get_setting(name, default)` (self-healing fallback)
- Migration helper `migrate_settings()` converts from RQ / `django-tasks-scheduler` config shapes

**Standalone mode:**
- In-memory config via `StandaloneConfig` class (`src/sqlery/fastapi_sqlery/config.py`)
- Programmatic init: `from sqlery.compat import initialize; initialize(database_url=..., max_workers=...)`
- Environment variables consumed by `StandaloneConfig._load_from_env()`:
  - `SQLERY_DATABASE_URL`
  - `SQLERY_POOL_SIZE`, `SQLERY_MAX_OVERFLOW`, `SQLERY_POOL_TIMEOUT`, `SQLERY_POOL_RECYCLE`
  - `DJANGO_SQL_JOBS_MAX_WORKERS`, `DJANGO_SQL_JOBS_ENABLE_DAEMON`, `DJANGO_SQL_JOBS_CHECK_INTERVAL`

**Project-level config files:**
- `pyproject.toml` — project metadata, deps, tool config (black, ruff, pytest, hatch)
- `alembic.ini` — Alembic migration config for standalone mode
- `Makefile` — dev automation
- `uv.lock` — pinned dependency lockfile

## CLI Entry Points

Declared in `[project.scripts]` of `pyproject.toml`:

| Command | Target | Purpose |
|---------|--------|---------|
| `sqlery` | `sqlery.core.cli:main` | Main Typer CLI |
| `sqlery-worker` | `sqlery.fastapi_sqlery.cli:worker_main` | Standalone worker |
| `sqlery-web` | `sqlery.fastapi_sqlery.cli:web_main` | Standalone dashboard server |
| `sqlery-daemon` | `sqlery.core.cli:daemon_main` | Daemon manager |
| `sqlery-jobs` | `sqlery.core.cli:jobs_main` | Job management |
| `sqlery-cleanup` | `sqlery.core.cli:cleanup_main` | DB retention cleanup |
| `sqlery-migrate` | `sqlery.core.cli:migrate_main` | Alembic migrations |
| `sqlery-tasks` | `sqlery.core.cli:tasks_main` | Scheduled-task management |
| `sqlery-queues` | `sqlery.core.cli:queues_main` | Queue management |

Django management commands (under `src/sqlery/django_sqlery/management/commands/`):
- `daemon`, `run_jobs`, `run_scheduled_tasks`, `cleanup_jobs`, `workers`, `rqworker`, `sqlery_export`, `sqlery_import`

## Database Support

**SQLite (dev / lightweight):**
- WAL mode + `busy_timeout` pragma auto-configured via Django `connection_created` signal in `src/sqlery/django_sqlery/apps.py`
- Standalone mode uses SQLAlchemy `StaticPool` with `check_same_thread=False` (`src/sqlery/fastapi_sqlery/database.py`)
- Concurrency relies on optimistic locking with a `version` column on `QueuedJob` (`src/sqlery/core/models.py`)
- Not recommended for production multi-worker setups

**PostgreSQL (production-recommended):**
- Atomic claiming via `SELECT FOR UPDATE SKIP LOCKED`
- Connection pooling via SQLAlchemy `QueuePool`: `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle=1800`, `pool_pre_ping=True`
- Configurable `PG_STATEMENT_TIMEOUT_MS` (default 30000) and `PG_LOCK_TIMEOUT_MS` (default 10000)
- CI tests run against PostgreSQL 15

**Migration tooling:**
- Django mode: Django migrations (`src/sqlery/django_sqlery/migrations/`)
- Standalone mode: Alembic (`alembic/`, `alembic.ini`)

## Platform Requirements

**Development:**
- Python 3.10+
- uv (recommended) or pip
- SQLite (built-in) or PostgreSQL 15+
- GNU Make for `Makefile` targets

**Production:**
- Python 3.10+
- PostgreSQL 15+ (recommended) or SQLite for trivial deployments
- Deployment targets: bare metal, Docker, AWS Lambda (serverless mode via `sqlery.lambda_handler`)
- CI: GitHub Actions on `ubuntu-latest` (`.github/workflows/test.yml`)

## Version Constraints

- Python: `>=3.10`
- Public API stability: `@job`, `enqueue`, `Queue` must remain backwards compatible (see `CLAUDE.md`)
- Fork-safety: DB connection lifecycle must survive `os.fork()` (parent/child resets)
- No new dependencies: prefer existing deps (`httpx`, `sqlmodel`, `asyncio`) over adding new
- Package version: `0.13.0` (from `pyproject.toml`)

---

*Stack analysis: 2026-05-13*
