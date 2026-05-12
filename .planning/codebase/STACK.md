# Technology Stack

**Analysis Date:** 2026-05-12

## Languages

**Primary:**
- Python 3.10+ - All source code, tests, and tooling
  - Uses modern syntax: `X | None` union types, `from collections.abc import Iterator`
  - Classified for 3.10, 3.11, 3.12, 3.13, 3.14

**Secondary:**
- HTML/Jinja2 - Dashboard templates (`src/sqlery/fastapi_sqlery/templates/`, `src/sqlery/django_sqlery/templates/`)
- CSS/JS - Dashboard static assets (`src/sqlery/django_sqlery/static/`)

## Runtime

**Environment:**
- CPython 3.10+ (minimum)
- Tested in CI against 3.11, 3.12, 3.13

**Package Manager:**
- uv (primary, used in CI and Makefile)
- pip-compatible (standard `pyproject.toml` with hatchling build backend)
- Lockfile: `uv.lock` present

## Frameworks

**Core:**
- Django >= 4.2 - Django integration mode (ORM, admin, management commands)
  - Classified for Django 4.2, 5.0, 6.0
  - Config: `src/sqlery/django_sqlery/apps.py` (`DjangoSqleryConfig`)
- FastAPI >= 0.104.0 - Standalone mode web dashboard and REST API
  - Config: `src/sqlery/fastapi_sqlery/app.py`
- SQLModel >= 0.0.14 - Standalone mode ORM (SQLAlchemy + Pydantic)
  - Models: `src/sqlery/core/models.py`
- SQLAlchemy (transitive via SQLModel) - Database engine, session management, connection pooling
  - Database init: `src/sqlery/fastapi_sqlery/database.py`

**Testing:**
- pytest >= 7.4.0 - Test runner
- pytest-django >= 4.5.0 - Django integration tests
- pytest-asyncio >= 0.23.0 - Async test support
- pytest-cov >= 4.1.0 - Coverage reporting
- pytest-timeout >= 2.2.0 - Test timeout enforcement
- hypothesis >= 6.92.0 - Property-based and chaos testing

**Build/Dev:**
- hatchling - Build backend (`pyproject.toml` `[build-system]`)
- black >= 23.0.0 - Code formatter (line-length 100, target py310)
- ruff >= 0.1.0 - Linter (line-length 100, target py310)
- uv - Package management and virtual environment

## Key Dependencies

**Critical (always required):**
- `croniter` >= 2.0.0 - Cron expression parsing and next-occurrence calculation
  - Used in: `src/sqlery/crontab.py` (vendored crontabula parser also present as fallback)
- `uuid6` >= 2024.1.0 - UUID v7 generation for time-sortable worker IDs
  - Used in: `src/sqlery/core/models.py`, `src/sqlery/django_sqlery/models.py`

**PostgreSQL support (optional):**
- `psycopg` >= 3.1 - PostgreSQL adapter (psycopg3, async-capable)

**Django mode:**
- `django` >= 4.2 - Full Django framework
- `django-tasks` >= 0.1.0 - Optional async task execution backend

**Standalone mode:**
- `sqlmodel` >= 0.0.14 - SQLAlchemy + Pydantic ORM layer
- `fastapi` >= 0.104.0 - Web framework for dashboard/API
- `uvicorn[standard]` >= 0.24.0 - ASGI server
- `jinja2` >= 3.1.0 - Template rendering for web dashboard
- `alembic` >= 1.12.0 - Database migrations for standalone mode
- `typer` >= 0.9.0 - CLI framework
- `rich` >= 13.0.0 - Terminal formatting for CLI output

**Optional feature extras:**
- `httpx` >= 0.24.0 - HTTP trigger mode (async HTTP client)
- `boto3` >= 1.34.0 - AWS EventBridge/Lambda trigger mode
- `requests` - Webhook delivery (referenced in `src/sqlery/webhooks.py` but not in pyproject.toml deps)

## Configuration

**Django mode:**
- Settings via `DJANGO_SQL_JOBS` dict in Django `settings.py`
- Full defaults in `src/sqlery/django_sqlery/settings.py` (`DEFAULTS` dict)
- `get_setting(name, default)` function for runtime access with self-healing fallbacks
- Migration helper: `migrate_settings()` for converting from RQ/django-tasks-scheduler config

**Standalone mode:**
- In-memory config via `StandaloneConfig` class (`src/sqlery/fastapi_sqlery/config.py`)
- Environment variables: `SQLERY_DATABASE_URL`, `SQLERY_POOL_SIZE`, `SQLERY_MAX_OVERFLOW`, `SQLERY_POOL_TIMEOUT`, `SQLERY_POOL_RECYCLE`, `DJANGO_SQL_JOBS_MAX_WORKERS`, `DJANGO_SQL_JOBS_ENABLE_DAEMON`, `DJANGO_SQL_JOBS_CHECK_INTERVAL`
- Programmatic init: `from sqlery.compat import initialize; initialize(database_url=..., max_workers=...)`

**Build:**
- `pyproject.toml` - Project metadata, dependencies, tool config (black, ruff, pytest)
- `alembic.ini` - Alembic migration configuration for standalone mode
- `Makefile` - Development automation (sample project, workers, stress tests)

## CLI Entry Points

Defined in `pyproject.toml` `[project.scripts]`:
- `sqlery` -> `src/sqlery/core/cli.py:main` - Main CLI (Typer-based)
- `sqlery-worker` -> `src/sqlery/fastapi_sqlery/cli.py:worker_main` - Standalone worker
- `sqlery-web` -> `src/sqlery/fastapi_sqlery/cli.py:web_main` - Dashboard web server
- `sqlery-daemon` -> `src/sqlery/core/cli.py:daemon_main` - Daemon manager
- `sqlery-jobs` -> `src/sqlery/core/cli.py:jobs_main` - Job management
- `sqlery-cleanup` -> `src/sqlery/core/cli.py:cleanup_main` - Database cleanup
- `sqlery-migrate` -> `src/sqlery/core/cli.py:migrate_main` - Alembic migrations
- `sqlery-tasks` -> `src/sqlery/core/cli.py:tasks_main` - Scheduled task management
- `sqlery-queues` -> `src/sqlery/core/cli.py:queues_main` - Queue management

## Database Support

**SQLite:**
- Default for development and lightweight deployments
- WAL mode and `busy_timeout` auto-configured via `connection_created` signal in Django mode (`src/sqlery/django_sqlery/apps.py`)
- Optimistic locking with version field for concurrent access (`src/sqlery/core/models.py` `QueuedJob.version`)
- `StaticPool` used for SQLAlchemy engine in standalone mode

**PostgreSQL:**
- Production-recommended database
- `SELECT FOR UPDATE SKIP LOCKED` for atomic job claiming
- Connection pooling via SQLAlchemy `QueuePool` (`pool_size`, `max_overflow`, `pool_pre_ping`)
- Configurable `statement_timeout` and `lock_timeout` (`src/sqlery/django_sqlery/settings.py`)
- CI tests run against PostgreSQL 15

## Platform Requirements

**Development:**
- Python 3.10+
- uv (recommended) or pip
- SQLite (built-in) or PostgreSQL
- GNU Make (for `Makefile` targets)

**Production:**
- Python 3.10+
- PostgreSQL 15+ (recommended) or SQLite
- Deployment targets: bare metal, Docker, AWS Lambda (serverless mode)
- CI: GitHub Actions with ubuntu-latest

---

*Stack analysis: 2026-05-12*
