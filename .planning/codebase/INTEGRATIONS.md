# External Integrations

**Analysis Date:** 2026-05-12

## APIs & External Services

**AWS Lambda (optional - eventbridge trigger mode):**
- Used for serverless job processing -- Lambda functions process jobs from the queue
- SDK/Client: `boto3` >= 1.34.0 (optional extra `eventbridge`)
- Implementation: `src/sqlery/eventbridge_trigger.py`
  - `invoke_lambda_worker()` - Async Lambda invocation for immediate job processing
  - `schedule_eventbridge_event()` - Delayed job scheduling via EventBridge rules
  - `ensure_cron_eventbridge_rule()` - Cron task scheduling via EventBridge rules
  - `delete_eventbridge_rule()` / `disable_cron_eventbridge_rule()` - Rule lifecycle management
- Lambda handler: `src/sqlery/lambda_handler.py`
  - Handler function: `sqlery.lambda_handler.handler`
  - Actions: `process_queue`, `run_scheduled_task`, `poll_and_process`
- Auth: AWS IAM credentials (boto3 default credential chain)
- Config keys:
  - `EVENTBRIDGE_LAMBDA_ARN` - ARN of the Lambda worker function (required)
  - `EVENTBRIDGE_BUS_NAME` - EventBridge bus name (default: `"default"`)
  - `AWS_REGION` - AWS region (optional, uses boto3 defaults)
- Example deployment: `examples/lambda/serverless.yml`

**AWS EventBridge (optional - eventbridge trigger mode):**
- Used for delayed job scheduling and cron-based task triggering
- Accessed via `boto3.client("events")` in `src/sqlery/eventbridge_trigger.py`
- Creates one-time rules for delayed jobs and recurring rules for cron tasks
- Rule naming convention: `sqlery-job-{job_id}-{timestamp}` (delayed) and `sqlery-cron-task-{task_id}` (cron)

**Webhook delivery (optional):**
- Outgoing HTTP POST notifications on job completion
- Implementation: `src/sqlery/webhooks.py`
  - `send_webhook()` - Single delivery attempt
  - `send_webhook_with_retry()` - Delivery with exponential backoff
  - `retry_failed_webhooks()` - Batch retry of failed deliveries
- HTTP client: `requests` library (imported at runtime, not a declared dependency)
- Security: HMAC-SHA256 signature via `X-Sqlery-Signature` header
- Config keys:
  - `WEBHOOK_SECRET` - Shared secret for HMAC signing
  - `WEBHOOK_TIMEOUT` - HTTP timeout in seconds (default: 10)

**HTTP trigger mode (optional - ASGI deployments):**
- Internal HTTP endpoint for triggering job processing
- Implementation stub: `src/sqlery/http_trigger_middleware.py` (migrated to `src/sqlery/django_sqlery/http_trigger_middleware.py`)
- HTTP client: `httpx` >= 0.24.0 (optional extra `http`)
- Config keys:
  - `INTERNAL_BASE_URL` - e.g., `http://127.0.0.1:8000`
  - `INTERNAL_SECRET` - Shared secret for HMAC signatures
  - `SIGNATURE_MAX_AGE` - Signature validity in seconds (default: 5)

## Data Storage

**Databases:**
- SQLite
  - Connection: In-memory (`:memory:` for tests) or file path
  - Client: Django ORM (Django mode) or SQLModel/SQLAlchemy (standalone mode)
  - Config: `DATABASES["default"]` in Django settings; `SQLERY_DATABASE_URL` env var in standalone
  - WAL mode and `busy_timeout` auto-configured in `src/sqlery/django_sqlery/apps.py`
  - Optimistic locking via `version` field on `QueuedJob`

- PostgreSQL 15+
  - Connection: `SQLERY_DATABASE_URL` or `DATABASE_URL` env var (e.g., `postgresql://user:pass@host:5432/dbname`)
  - Client: Django ORM with `psycopg` adapter (Django mode) or SQLModel/SQLAlchemy with `psycopg` (standalone)
  - Connection pooling: SQLAlchemy `QueuePool` with `pool_pre_ping=True`
  - Pool config: `SQLERY_POOL_SIZE` (default 5), `SQLERY_MAX_OVERFLOW` (default 10), `SQLERY_POOL_TIMEOUT` (default 30), `SQLERY_POOL_RECYCLE` (default 1800)
  - Locking: `SELECT FOR UPDATE SKIP LOCKED` for atomic job claiming
  - Configurable: `PG_STATEMENT_TIMEOUT_MS` (default 30000), `PG_LOCK_TIMEOUT_MS` (default 10000)

**Schema migrations:**
- Django mode: Django migrations in `src/sqlery/django_sqlery/migrations/` (24 migrations: 0001 through 0024)
- Standalone mode: Alembic migrations in `alembic/versions/` (13 migrations)
- Tables: `sqlery_queued_job`, `sqlery_scheduled_task`, `sqlery_worker`, `sqlery_registry`

**File Storage:**
- Not applicable (no file storage integration)

**Caching:**
- None (database-backed queue, no external cache layer)

## Authentication & Identity

**Auth Provider:**
- No external auth provider
- Django admin authentication used for Django mode dashboard access
- Webhook authentication via HMAC-SHA256 signatures (`src/sqlery/webhooks.py`)
- HTTP trigger mode uses HMAC signatures for internal endpoint security

## Monitoring & Observability

**Error Tracking:**
- None (no external error tracking service)
- Errors stored in `QueuedJob.error` and `QueuedJob.traceback` fields

**Logs:**
- Standard Python `logging` module throughout
- Logger per module: `logging.getLogger(__name__)`
- No structured logging or external log aggregation configured
- Rich terminal output for CLI commands (`src/sqlery/core/cli.py`)

**Health check:**
- FastAPI `/health` endpoint in standalone mode (`src/sqlery/fastapi_sqlery/app.py`)
- Worker heartbeat system: workers send periodic heartbeats stored in `sqlery_worker` table
  - Configurable interval: `WORKER_HEARTBEAT_INTERVAL` (default 5s)
  - Dead detection: `WORKER_ALIVE_TIMEOUT` (default 30s)

## CI/CD & Deployment

**Hosting:**
- No specific hosting platform configured
- Deployment targets documented:
  - Bare metal / traditional server (Django mode with management commands)
  - Docker (stress test `compose.yml` at `stress_test/compose.yml`)
  - AWS Lambda (serverless mode via `src/sqlery/lambda_handler.py`)

**CI Pipeline:**
- GitHub Actions (`.github/workflows/test.yml`)
- Matrix: Python 3.11, 3.12, 3.13 on ubuntu-latest
- Services: PostgreSQL 15
- Uses `uv` for dependency installation
- Test stages:
  1. Unit tests (`tests/` excluding `tests/chaos/`)
  2. Chaos/property tests (`tests/chaos/`)
  3. PostgreSQL-specific tests (`test_atomic_claiming.py`, `test_atomic_scheduler.py`)
  4. Coverage report (`--cov=src/sqlery`)

## Environment Configuration

**Required env vars (standalone mode):**
- `SQLERY_DATABASE_URL` - Database connection URL (PostgreSQL or SQLite)

**Required env vars (Django mode):**
- `DJANGO_SETTINGS_MODULE` - Django settings module path
- Standard Django database configuration in `DATABASES`

**Required env vars (Lambda mode):**
- `DJANGO_SETTINGS_MODULE` - Django settings module
- `DATABASE_URL` - Database connection URL
- AWS credentials (via IAM role or env vars)

**Optional env vars:**
- `SQLERY_POOL_SIZE` - Connection pool size (default 5)
- `SQLERY_MAX_OVERFLOW` - Max overflow connections (default 10)
- `SQLERY_POOL_TIMEOUT` - Pool timeout seconds (default 30)
- `SQLERY_POOL_RECYCLE` - Connection recycle seconds (default 1800)
- `DJANGO_SQL_JOBS_MAX_WORKERS` - Worker count per node
- `DJANGO_SQL_JOBS_ENABLE_DAEMON` - Enable/disable daemon
- `DJANGO_SQL_JOBS_CHECK_INTERVAL` - Daemon check interval

**Secrets location:**
- Environment variables (no secrets file management built-in)
- `.makefile-configs/` directory contains example `.env` files for various configurations:
  - `default.env.example`, `eventbridge.env.example`, `http-trigger.env.example`, `multi-worker.env.example`, `queue-high.env.example`, `queue-low.env.example`

## Webhooks & Callbacks

**Incoming:**
- HTTP trigger endpoint (Django mode) - receives internal requests to trigger job processing
  - Implementation: `src/sqlery/django_sqlery/http_trigger_middleware.py`
  - HMAC-signed requests for security

**Outgoing:**
- Job completion webhooks (`src/sqlery/webhooks.py`)
  - Events: `success`, `failure`
  - Payload includes: job ID, task path, status, timing, output/error, tags
  - HMAC-SHA256 signature in `X-Sqlery-Signature` header
  - Configurable retry with exponential backoff
  - Webhook URL and events configured per-job (`webhook_url`, `webhook_events` fields on Django `QueuedJob` model)
  - Batch retry via `retry_failed_webhooks()` function

## Compatibility Layers

**RQ (Redis Queue) compatibility:**
- Drop-in replacement module: `src/sqlery/compat/rq.py`
- Provides: `Queue`, `get_queue`, `Retry`, `get_current_job` matching RQ API
- Status: Deprecated since v3.1.0, removal planned for v3.2.0

**django-tasks-scheduler compatibility:**
- Drop-in replacement module: `src/sqlery/compat/scheduler.py`
- Provides: `Task`, `TaskType`, `TaskArg`, `TaskKwarg`, `Queue`, `get_queue`, `get_all_workers`, `JobModel`, `JobStatus`
- Status: Deprecated since v3.1.0, removal planned for v3.2.0

**django-tasks integration:**
- Optional backend for async task execution
- Extra: `tasks` (`django-tasks >= 0.1.0`)
- Config: `USE_DJANGO_TASKS` setting (default True)

---

*Integration audit: 2026-05-12*
