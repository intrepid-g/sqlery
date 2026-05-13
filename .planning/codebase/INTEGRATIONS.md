# External Integrations

**Analysis Date:** 2026-05-13

## APIs & External Services

**AWS (serverless / EventBridge mode):**
- AWS Lambda — serverless job execution
  - Handler: `sqlery.lambda_handler.handler` (`src/sqlery/lambda_handler.py`)
  - SDK: `boto3` >= 1.34.0 (optional extra `eventbridge`, guarded `try: import boto3`)
  - Auth: standard AWS credential chain (env vars, instance role, etc.); no library-specific env var
- AWS EventBridge — delayed / scheduled job dispatch via Lambda invocation
  - Implementation: `src/sqlery/eventbridge_trigger.py`
  - `invoke_lambda_worker(job_id, queue_name)` directly invokes Lambda
  - Cron jobs schedule EventBridge rules for next execution
  - Configured via `DJANGO_SQL_JOBS["TRIGGER_MODE"] = "eventbridge"` + `EVENTBRIDGE_LAMBDA_ARN`, `EVENTBRIDGE_BUS_NAME`, `AWS_REGION`

**HTTP webhooks (outbound):**
- Implementation: `src/sqlery/webhooks.py`
- Library: `requests` (NOT declared in `pyproject.toml`, guarded `try: import requests`)
- Outbound notifications on job lifecycle events
- Signed with HMAC-SHA256 — see `generate_webhook_signature(payload, secret)`
- Custom JSON encoder `_SafeEncoder` handles UUID, datetime, Decimal, bytes, set/frozenset

**HTTP trigger (internal, ASGI deployments):**
- Implementation: `src/sqlery/http_trigger_middleware.py`, `src/sqlery/django_sqlery/http_trigger_middleware.py`
- Library: `httpx` >= 0.24.0 (optional extra `http`)
- Triggers worker/scheduler runs by hitting a signed internal URL on the running ASGI app
- Settings: `INTERNAL_BASE_URL`, `INTERNAL_SECRET`, `SIGNATURE_MAX_AGE` (default 5s)
- Activated via `DJANGO_SQL_JOBS["TRIGGER_MODE"] = "http"`

## Data Storage

**Databases:**
- PostgreSQL (production) — via `psycopg` >= 3.1
  - Standalone: `SQLERY_DATABASE_URL` env var
  - Django: `DATABASES` setting (standard Django)
- SQLite (dev / lightweight) — built into Python
  - WAL mode + busy_timeout auto-configured
  - Single-file storage

**ORM clients:**
- Django ORM (Django mode) — `src/sqlery/django_sqlery/models.py`, `backend.py`
- SQLModel / SQLAlchemy (standalone) — `src/sqlery/core/models.py`, `src/sqlery/fastapi_sqlery/backend.py`, `database.py`

**File Storage:**
- Local filesystem only (no S3 / GCS client)

**Caching:**
- None — all state lives in the database (queue, scheduler, worker heartbeats, leases)

## Authentication & Identity

**Internal HTTP trigger:**
- HMAC-SHA256 shared-secret signatures
- Signature lifetime controlled by `SIGNATURE_MAX_AGE` (default 5s)
- Implementation: `src/sqlery/signature.py`, `src/sqlery/django_sqlery/signature.py`

**Webhook outbound:**
- HMAC-SHA256 signatures generated in `src/sqlery/webhooks.py:generate_webhook_signature`

**Web dashboard:**
- Django: relies on Django admin auth (`src/sqlery/django_sqlery/admin.py`, `admin_site.py`)
- Standalone (FastAPI): no built-in auth on dashboard endpoints — deployment responsibility

## Monitoring & Observability

**Error Tracking:**
- None bundled — logging only

**Logs:**
- Python `logging` initialized per-module (`logger = logging.getLogger(__name__)`)
- Log helpers in `src/sqlery/core/log_config.py`
- Levels follow project convention (DEBUG / INFO / WARNING / ERROR / EXCEPTION with f-string messages)

**Job lifecycle:**
- RQ-style registry manager (`src/sqlery/core/registry.py`) tracks finished/failed/started/canceled/scheduled/deferred jobs in DB
- Worker heartbeats stored in DB rows (no external metrics backend)

## CI/CD & Deployment

**Hosting:**
- Library — distributed via PyPI (`hatchling` build, `uv` publishing)
- Application targets: bare metal, Docker, AWS Lambda (serverless mode), any ASGI host (for FastAPI dashboard)

**CI Pipeline:**
- GitHub Actions — `.github/workflows/test.yml`
- Runs against Python 3.11–3.13 and both SQLite and PostgreSQL 15
- Uses `uv` for environment setup

## Environment Configuration

**Standalone mode env vars (read by `StandaloneConfig._load_from_env` in `src/sqlery/fastapi_sqlery/config.py`):**
- `SQLERY_DATABASE_URL` — DB connection string
- `SQLERY_POOL_SIZE`, `SQLERY_MAX_OVERFLOW`, `SQLERY_POOL_TIMEOUT`, `SQLERY_POOL_RECYCLE` — SQLAlchemy QueuePool tuning
- `DJANGO_SQL_JOBS_MAX_WORKERS`, `DJANGO_SQL_JOBS_ENABLE_DAEMON`, `DJANGO_SQL_JOBS_CHECK_INTERVAL` — daemon/worker overrides

**Django mode env vars:**
- Standard Django (`DJANGO_SETTINGS_MODULE`, `DATABASE_URL` when used by host project)
- `DJANGO_SETTINGS_MODULE` required by `sqlery.lambda_handler` to bootstrap Django in Lambda

**AWS env vars (for EventBridge / Lambda):**
- Standard AWS SDK chain (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, etc.) — not read directly by sqlery
- `DJANGO_SQL_JOBS["AWS_REGION"]` overrides for the EventBridge client if set

**Secrets:**
- `DJANGO_SQL_JOBS["INTERNAL_SECRET"]` — HMAC secret for internal HTTP trigger
- Webhook secrets — passed per-webhook to `generate_webhook_signature`
- No `.env` file convention enforced by the library

## Webhooks & Callbacks

**Outgoing:**
- Job lifecycle webhooks dispatched from `src/sqlery/webhooks.py`
- HMAC-SHA256 signed payload; JSON body via `_SafeEncoder`
- Triggered on job completion / failure events

**Incoming:**
- Internal HTTP trigger endpoints (`src/sqlery/django_sqlery/urls.py`, `src/sqlery/django_sqlery/api_views.py`, `src/sqlery/django_sqlery/views.py`)
- FastAPI REST API surface in `src/sqlery/fastapi_sqlery/app.py`
- Both protected by signed-URL / shared-secret signature for the trigger endpoints; dashboard endpoints rely on host auth

## Django Integration Surface

- App: `'sqlery.django_sqlery'` added to `INSTALLED_APPS`
- AppConfig: `src/sqlery/django_sqlery/apps.py` — registers SQLite WAL signal handler, admin
- Models: 24+ migrations in `src/sqlery/django_sqlery/migrations/` (`QueuedJob`, `ScheduledTask`, `Worker`/`WorkerProcess`, `DaemonLease`, etc.)
- Admin: `src/sqlery/django_sqlery/admin.py`, custom site in `admin_site.py`
- Dashboard views: `src/sqlery/django_sqlery/dashboard_views.py`
- Middleware: `src/sqlery/django_sqlery/middleware.py`, `daemon_middleware.py`, `http_trigger_middleware.py`, `subprocess_middleware.py`
- Backend implementation: `src/sqlery/django_sqlery/backend.py` (implements `DatabaseBackend` ABC)
- Management commands: `daemon`, `run_jobs`, `run_scheduled_tasks`, `cleanup_jobs`, `workers`, `rqworker`, `sqlery_export`, `sqlery_import`
- Optional async execution via `django-tasks` (extra `tasks`); guarded import in `src/sqlery/triggers.py`

## Standalone (SQLAlchemy + FastAPI) Surface

- Package: `src/sqlery/fastapi_sqlery/`
- Backend: `backend.py` (implements `DatabaseBackend` ABC against SQLModel)
- DB session/engine: `database.py` — `init_database(database_url, **kwargs)`, `get_engine()`, `StaticPool` for SQLite, `QueuePool` for PostgreSQL
- Web app: `app.py` — FastAPI dashboard + REST API
- CLI: `cli.py` — `worker_main`, `web_main`
- Config: `config.py` — `StandaloneConfig` with env-var loading
- Models: shared `src/sqlery/core/models.py` (SQLModel definitions)
- Migrations: Alembic (`alembic.ini`, `alembic/versions/`)
- Programmatic bootstrap: `from sqlery.compat import initialize; initialize(database_url=..., max_workers=...)`

## Compatibility Layers

**RQ (Redis Queue) drop-in:**
- Module: `src/sqlery/compat/rq.py`
- Provides `Retry`, `get_queue`, `get_current_job`, `Queue` aliases backed by `DjangoQueue` + `DjangoBackend`
- Deprecated since v3.1.0, scheduled for removal in v3.2.0 (`DeprecationWarning` emitted on import)
- Migration: change imports only — keeps RQ codebases drop-in compatible

**django-tasks-scheduler drop-in:**
- Module: `src/sqlery/compat/scheduler.py`
- Provides `Task`, `TaskType`, `TaskArg`, `TaskKwarg`, `get_scheduled_task`, `run_task`, `job`, `Queue`, `get_queue`, `get_all_workers`, `JobModel`, `JobStatus`
- Same deprecation status as the RQ shim

**Internal compat (mode auto-detection):**
- `src/sqlery/compat/__init__.py` — `DatabaseBackend` and `Config` ABCs, `get_backend()`, `get_config()`, `initialize()`
- Detects Django via `from django.conf import settings as _django_settings` import, falls back to standalone
- Singletons `_backend` and `_config` initialized once per process

## Config Surfaces (summary)

| Surface | Where | Loader |
|---------|-------|--------|
| Django settings dict | `settings.DJANGO_SQL_JOBS` | `get_setting()` in `src/sqlery/django_sqlery/settings.py` |
| Env vars (standalone) | OS environment | `StandaloneConfig._load_from_env()` in `src/sqlery/fastapi_sqlery/config.py` |
| Programmatic init | Python code | `sqlery.compat.initialize(...)` |
| Alembic | `alembic.ini` | Alembic CLI / `sqlery-migrate` |
| Pytest / Django test settings | `tests/settings.py` | `[tool.pytest.ini_options]` in `pyproject.toml` |

## CLI ↔ External System Mapping

| CLI Entry | External Surface |
|-----------|------------------|
| `sqlery-worker` | Reads jobs from DB (PG/SQLite); forks child processes |
| `sqlery-web` | Boots Uvicorn ASGI server hosting FastAPI dashboard/API |
| `sqlery-daemon` | Long-running daemon: scheduler + worker pool + DB leases |
| `sqlery-migrate` | Invokes Alembic migrations against `SQLERY_DATABASE_URL` |
| `sqlery-jobs` / `sqlery-tasks` / `sqlery-queues` | DB-only CRUD over standalone tables |
| `sqlery-cleanup` | Applies retention policies, deletes old rows |
| Django `manage.py daemon` / `run_jobs` / `workers` | Same as above but inside Django process |
| `sqlery.lambda_handler.handler` | AWS Lambda invocation entry (EventBridge events or direct invocation) |

---

*Integration audit: 2026-05-13*
