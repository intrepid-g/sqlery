<!-- refreshed: 2026-05-13 -->
# Architecture

**Analysis Date:** 2026-05-13

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          Public API Layer                            │
│  @job decorator    enqueue()/enqueue_at()    Queue    Worker         │
│  `src/sqlery/__init__.py`  `src/sqlery/core/job.py`                  │
│                            `src/sqlery/core/job_queue.py`            │
└─────────────────┬───────────────────────────────────┬───────────────┘
                  │                                   │
                  ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Core Logic Layer (framework-agnostic)          │
│  WorkerProcess   JobExecutor   DaemonManager   Scheduler             │
│  Claiming algo   RegistryMgr   CleanupMgr      CLI (Typer)           │
│  `src/sqlery/core/worker.py` `daemon.py` `scheduler.py`              │
│  `claiming.py` `worker_pool.py` `registry.py` `cleanup.py` `cli.py`  │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                Compat Layer (mode auto-detection)                    │
│  DatabaseBackend ABC    Config ABC    get_backend()/get_config()     │
│  initialize()           RQ shim       django-tasks-scheduler shim    │
│  `src/sqlery/compat/__init__.py` `compat/rq.py` `compat/scheduler.py`│
└─────────┬───────────────────────────────────────────────────┬───────┘
          │                                                   │
          ▼                                                   ▼
┌────────────────────────────────────┐  ┌─────────────────────────────┐
│  Django Integration                 │  │  Standalone Integration     │
│  DjangoBackend / DjangoConfig       │  │  SQLAlchemyBackend          │
│  Django ORM models + 25 migrations  │  │  StandaloneConfig           │
│  Admin + dashboard + mgmt commands  │  │  SQLModel + 13 Alembic mig. │
│  `src/sqlery/django_sqlery/`        │  │  FastAPI app + CLI          │
│                                     │  │  `src/sqlery/fastapi_sqlery/`│
└─────────────────────┬───────────────┘  └────────┬────────────────────┘
                      │                            │
                      ▼                            ▼
              ┌──────────────────────────────────────────┐
              │   PostgreSQL (prod) / SQLite (dev)       │
              └──────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| JobWrapper / `@job` | Decorator that wraps functions as enqueueable jobs | `src/sqlery/core/job.py` |
| Queue / enqueue | Enqueue jobs via the active backend | `src/sqlery/core/job_queue.py` |
| DatabaseBackend ABC | Abstract interface for all DB operations | `src/sqlery/compat/__init__.py` |
| DjangoBackend | Django ORM implementation of `DatabaseBackend` | `src/sqlery/django_sqlery/backend.py` |
| SQLAlchemyBackend | SQLModel/SQLAlchemy implementation of `DatabaseBackend` | `src/sqlery/fastapi_sqlery/backend.py` |
| DjangoConfig | Reads config from `settings.DJANGO_SQL_JOBS` | `src/sqlery/django_sqlery/config.py` |
| StandaloneConfig | In-memory config + env-var loading | `src/sqlery/fastapi_sqlery/config.py` |
| JobExecutor | Executes a single job (retry, timeout, crash recovery) | `src/sqlery/core/worker.py` |
| WorkerProcess | Persistent worker: polls, forks children, monitors | `src/sqlery/core/worker.py` |
| WorkerPoolManager | Manages pool of worker subprocesses | `src/sqlery/core/worker_pool.py` |
| DaemonManager | Top-level daemon: scheduler + pool + heartbeats + leases | `src/sqlery/core/daemon.py` |
| Scheduler | Finds due `ScheduledTask`s and enqueues jobs | `src/sqlery/core/scheduler.py`, `src/sqlery/core/scheduler_tasks.py` |
| Claiming Algorithm | Tag concurrency, rate limits, deps, atomic claim | `src/sqlery/core/claiming.py` |
| RegistryManager | RQ-compatible job lifecycle registries | `src/sqlery/core/registry.py` |
| CleanupManager | Retention-based job/registry cleanup | `src/sqlery/core/cleanup.py` |
| CLI (Typer) | Standalone CLI entry points | `src/sqlery/core/cli.py` |
| DB resilience | Retry decorator, WAL/timeout config | `src/sqlery/core/db_resilience.py` |
| FastAPI dashboard | Web UI + REST API for standalone mode | `src/sqlery/fastapi_sqlery/app.py` |
| Standalone CLI shim | `sqlery-worker` / `sqlery-web` entry points | `src/sqlery/fastapi_sqlery/cli.py` |
| Engine / session | Global SQLAlchemy engine + session factory | `src/sqlery/fastapi_sqlery/database.py` |
| Django admin/dashboard | Admin views + custom dashboard | `src/sqlery/django_sqlery/admin.py`, `src/sqlery/django_sqlery/dashboard_views.py` |
| Django mgmt commands | `daemon`, `run_jobs`, `workers`, `cleanup_jobs`, … | `src/sqlery/django_sqlery/management/commands/` |
| Lambda handler | AWS Lambda entry point (Django mode) | `src/sqlery/lambda_handler.py` |
| RQ shim | Drop-in for `rq.Queue`, `Retry`, `get_current_job` | `src/sqlery/compat/rq.py` |
| Scheduler shim | Drop-in for django-tasks-scheduler | `src/sqlery/compat/scheduler.py` |

## Pattern Overview

**Overall:** Strategy pattern over a `DatabaseBackend` ABC, with mode auto-detection at process startup. Fork-per-job execution model inspired by RQ. Database is the single source of truth for queue state, scheduler state, worker heartbeats, and daemon leases.

**Key Characteristics:**
- Abstract `DatabaseBackend` ABC with 30+ methods defines the contract
- `DjangoBackend` and `SQLAlchemyBackend` are the two concrete implementations
- `get_backend()` returns a process-local singleton (lazy initialisation in `src/sqlery/compat/__init__.py`)
- All business logic in `src/sqlery/core/` is framework-agnostic and delegates to the backend
- Fork-per-job for crash isolation and memory safety
- Database-backed everything: queue, scheduler, worker heartbeats, leases

## Layers

**Public API:**
- Purpose: User-facing decorators and functions for enqueueing jobs
- Location: `src/sqlery/__init__.py`, `src/sqlery/core/job.py`, `src/sqlery/core/job_queue.py`
- Contains: `@job` decorator, `enqueue()`, `enqueue_at()`, `get_queue()`, `Queue`, `Worker` alias
- Depends on: Compat layer (`get_backend`, `get_config`)
- Used by: Application code

**Core logic (framework-agnostic):**
- Purpose: Business logic for job processing
- Location: `src/sqlery/core/`
- Contains: Worker, Scheduler, Claiming algorithm, Daemon, Registry, Cleanup, CLI, DB resilience, utils
- Depends on: Compat layer (DatabaseBackend, Config)
- Used by: CLI, Django mgmt commands, FastAPI app, Lambda handler

**Compat:**
- Purpose: Auto-detects mode and provides unified interfaces
- Location: `src/sqlery/compat/__init__.py`
- Contains: `DatabaseBackend` ABC, `Config` ABC, `get_backend()`, `get_config()`, `initialize()`, plus RQ and scheduler shims
- Depends on: Backend implementations (lazy import to avoid circular Django app-loading)
- Used by: All core logic and the public API

**Django integration:**
- Purpose: Django-specific models, admin, mgmt commands, middleware
- Location: `src/sqlery/django_sqlery/`
- Contains: ORM models, 25 migrations, `DjangoBackend`, admin site, dashboard, mgmt commands (`daemon.py`, `run_jobs.py`, `workers.py`, `cleanup_jobs.py`, `run_scheduled_tasks.py`, `rqworker.py`, `sqlery_import.py`, `sqlery_export.py`)
- Depends on: Django ORM, core (via compat)
- Used by: Django projects

**Standalone (FastAPI) integration:**
- Purpose: SQLModel models, FastAPI dashboard, Alembic migrations
- Location: `src/sqlery/fastapi_sqlery/`, plus shared core models at `src/sqlery/core/models.py`
- Contains: SQLModel models, `SQLAlchemyBackend`, FastAPI app, CLI shim, engine/session factory
- Depends on: SQLModel/SQLAlchemy, FastAPI, core (via compat)
- Used by: Non-Django Python projects

**Backward-compat shims:**
- Purpose: Drop-in replacements for RQ and django-tasks-scheduler
- Location: `src/sqlery/compat/rq.py`, `src/sqlery/compat/scheduler.py`
- Contains: RQ-compatible `Queue`, `Retry`, `get_current_job`; scheduler-compatible `Task`, `TaskType`
- Depends on: Django integration layer
- Used by: Projects migrating from RQ or django-tasks-scheduler

## Data Flow

### Primary Request Path: Enqueueing a Job

1. App calls `my_task.enqueue(...)` or `enqueue("module.path", kwargs)` (`src/sqlery/core/job.py`, `src/sqlery/core/job_queue.py`)
2. `Queue` resolves the active backend via `get_backend()` (`src/sqlery/compat/__init__.py`)
3. Backend serialises kwargs, inserts row into `sqlery_queued_job` (`src/sqlery/django_sqlery/backend.py:create_job` or `src/sqlery/fastapi_sqlery/backend.py:create_job`)
4. Job ID returned to caller; transaction commits

### Primary Execution Path: Processing a Job

1. `DaemonManager` cycle runs the scheduler step then maintains the worker pool (`src/sqlery/core/daemon.py`)
2. `WorkerPoolManager` spawns workers as subprocesses via `python -m sqlery.core.worker_runner` (`src/sqlery/core/worker_pool.py`, `src/sqlery/core/worker_runner.py`)
3. `WorkerProcess.run()` polls; for each iteration it invokes the claiming algorithm (`src/sqlery/core/claiming.py`) which enforces tag concurrency, rate limits, dependencies, then atomically claims a job (Postgres: `SELECT FOR UPDATE SKIP LOCKED`; SQLite: version-CAS)
4. Parent calls `_reset_db_connections()` then `os.fork()`; child calls `os.setpgrp()`, sets `SIGALRM` for `timeout_seconds`, executes the task callable (`src/sqlery/core/worker.py` `JobExecutor`)
5. Child writes result/exception to DB and `_exit(0)`; parent `waitpid`s and marks job finished/failed with retry scheduling
6. On timeout the parent sends `SIGTERM`/`SIGKILL` to the child's process group (safety net at `timeout + 60s`)

### Scheduled Task Path

1. `Scheduler.tick()` queries `ScheduledTask` rows whose `next_run <= now` (`src/sqlery/core/scheduler.py`, `src/sqlery/core/scheduler_tasks.py`)
2. For each due task it computes the next cron occurrence via `croniter` and atomically updates `next_run` (optimistic check on previous `next_run`)
3. A `QueuedJob` is created with `scheduled_task_id` set; idempotency prevents duplicate enqueues for the same tick
4. Standard execution path takes over

### Serverless / Lambda Path

1. EventBridge triggers Lambda at the configured interval (`src/sqlery/eventbridge_trigger.py`)
2. `lambda_handler(event, context)` (`src/sqlery/lambda_handler.py`) initialises Django, runs one scheduler tick and one bounded claim/execute cycle, then exits
3. No persistent worker process — each invocation claims and executes a small batch
4. State remains in the database between invocations

**State Management:**
- All state lives in the database (PostgreSQL or SQLite). No in-memory queues.
- Worker heartbeats are DB rows refreshed via SIGUSR1 signals from the daemon.
- Queue ownership uses DB-backed leases (`DaemonLease` model) with TTL-based expiry.
- Optimistic locking via `version` field on `QueuedJob` (CAS) for SQLite.
- Postgres claiming uses `SELECT FOR UPDATE SKIP LOCKED`.

## Key Abstractions

**DatabaseBackend:**
- Purpose: Unified interface for all DB operations across Django/SQLAlchemy
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
- Pattern: Strategy with runtime auto-detection (Django detected if `django.conf.settings` is configured)

**JobWrapper (`@job`):**
- Purpose: Turns a function into an enqueueable task exposing `.enqueue()`, `.delay()`, `.enqueue_at()`
- Examples: `src/sqlery/core/job.py`, `src/sqlery/django_sqlery/__init__.py` (Django variant with `async_job`)
- Pattern: Decorator that preserves the original callable (direct call still executes synchronously)

**Config:**
- Purpose: Unified configuration interface across Django settings and standalone env vars
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/config.py`, `src/sqlery/fastapi_sqlery/config.py`
- Pattern: Strategy; Django reads `settings.DJANGO_SQL_JOBS`, standalone uses in-memory dict overlaid with `SQLERY_*` env vars

**Fork-per-job execution:**
- Purpose: Memory-safe job execution with crash isolation (RQ-style)
- Examples: `src/sqlery/core/worker.py`
- Pattern: Parent claims a job and forks a child. Child runs one job, writes the result, exits. Parent is never blocked by job code; child crashes cannot poison the worker.

## Entry Points

**Python package:**
- Location: `src/sqlery/__init__.py`
- Triggers: `from sqlery import enqueue, Queue, Worker, job`
- Responsibilities: Re-exports core API; conditionally imports Django decorators and `AsyncQueue`

**Standalone CLI (Typer):**
- Location: `src/sqlery/core/cli.py` (+ thin shim at `src/sqlery/fastapi_sqlery/cli.py`)
- Triggers: `pyproject.toml [project.scripts]`: `sqlery`, `sqlery-worker`, `sqlery-web`, `sqlery-daemon`, `sqlery-jobs`, `sqlery-cleanup`, `sqlery-migrate`, `sqlery-tasks`, `sqlery-queues`
- Responsibilities: Daemon, workers, jobs, tasks, cleanup, Alembic migrations

**Worker runner subprocess:**
- Location: `src/sqlery/core/worker_runner.py`
- Triggers: Spawned by `WorkerPoolManager.spawn_worker()` as `python -m sqlery.core.worker_runner`
- Responsibilities: Initialise Django if present, build a `WorkerProcess`, run the poll loop

**FastAPI app:**
- Location: `src/sqlery/fastapi_sqlery/app.py`
- Triggers: `sqlery-web` CLI or `uvicorn sqlery.fastapi_sqlery.app:app`
- Responsibilities: Web UI dashboard + REST API for standalone mode

**Django AppConfig:**
- Location: `src/sqlery/django_sqlery/apps.py`
- Triggers: Adding `'sqlery.django_sqlery'` to `INSTALLED_APPS`
- Responsibilities: Connects `connection_created` signal (enables SQLite WAL + `busy_timeout`), registers admin

**Django management commands:**
- Location: `src/sqlery/django_sqlery/management/commands/` (with a transitional copy at `src/sqlery/management/commands/`)
- Triggers: `python manage.py daemon`, `run_jobs`, `workers`, `cleanup_jobs`, `run_scheduled_tasks`, `rqworker`, `sqlery_import`, `sqlery_export`
- Responsibilities: Django-style CLI for daemon, workers, scheduler, cleanup, import/export

**AWS Lambda:**
- Location: `src/sqlery/lambda_handler.py`
- Triggers: AWS Lambda invocation from EventBridge (`src/sqlery/eventbridge_trigger.py`)
- Responsibilities: Serverless scheduler + claim/execute cycle (Django mode only)

## Architectural Constraints

- **Threading:** Single-threaded event loop per worker. Workers fork child processes for execution; no threading inside the worker. SIGUSR1 handlers only set flags (no DB calls in signal handlers, to avoid corrupting psycopg connections).
- **Global state:** Module-level singletons `_backend` and `_config` in `src/sqlery/compat/__init__.py` (initialised once per process). Global SQLAlchemy `_engine` in `src/sqlery/fastapi_sqlery/database.py`.
- **Circular imports:** Carefully managed. Django decorators are imported conditionally in `src/sqlery/__init__.py`. The compat layer uses absolute imports (e.g. `from sqlery.django_sqlery.backend import ...`) to avoid relative resolution into the wrong package during Django app loading. RQ/scheduler shims are intentionally not imported eagerly.
- **Fork safety:** DB connections must be closed before `os.fork()` and reopened in both parent and child. `_reset_db_connections()` calls `django.db.connections.close_all()` (Django) or disposes the SQLAlchemy engine (standalone). Child calls `os.setpgrp()` for process-group isolation.
- **SQLite limitations:** No `SELECT FOR UPDATE SKIP LOCKED` — uses optimistic locking with `QueuedJob.version` (CAS). WAL mode enabled and `busy_timeout` pragma set to 5000 ms via `connection_created` signal. Not recommended for production multi-worker setups.
- **Signal handling:** SIGTERM/SIGINT for graceful shutdown (forwarded to child process groups via `os.killpg`). SIGUSR1 for heartbeat requests from daemon to workers. SIGALRM in the child for job timeout.
- **Backward compatibility:** Top-level stub modules (`src/sqlery/models.py`, `executor.py`, `decorators.py`, `utils.py`, plus the entire flat top-level layout — `worker.py`, `queue.py`, `daemon_manager.py`, etc.) re-export from new locations. Do not delete — comment-and-date per the project's dead-code policy.

## Anti-Patterns

### Importing Django models at module level in core code

**What happens:** A module under `src/sqlery/core/` directly does `from sqlery.django_sqlery.models import QueuedJob`.
**Why it's wrong:** Core must remain framework-agnostic. Direct imports break standalone mode and create circular imports during Django app loading.
**Do this instead:** Go through `get_backend()` from `src/sqlery/compat/__init__.py`; backends return generic dict/dataclass-like job records.

### Duplicated model definitions

**What happens:** Adding a new field to `QueuedJob` in only one of `src/sqlery/django_sqlery/models.py` or `src/sqlery/core/models.py`.
**Why it's wrong:** The two integration modes silently diverge; tests on one backend miss bugs on the other.
**Do this instead:** Update both model files together, write a matching Django migration in `src/sqlery/django_sqlery/migrations/` and an Alembic revision in `alembic/versions/`, and reflect the field in the `DatabaseBackend` ABC.

### DB calls inside signal handlers

**What happens:** A SIGUSR1/SIGTERM handler updates worker state via the ORM.
**Why it's wrong:** Signals can fire mid-transaction and corrupt psycopg connections.
**Do this instead:** Set a flag in the handler and process it from the worker's main loop, as done in `src/sqlery/core/worker.py`.

## Error Handling

- **Job-level:** Try/except around task execution; mark job failed with error + traceback; retry with exponential backoff when `max_retries > 0` (`src/sqlery/core/worker.py`).
- **Worker-level:** Unhandled errors in the main loop are caught, DB connections reset, sleep, continue. On shutdown the worker marks `status='dead'`.
- **Daemon-level:** Each daemon cycle step is wrapped in try/except; errors are logged and the loop continues. Signal-based graceful shutdown (`src/sqlery/core/daemon.py`).
- **Fork-level:** Parent kills the child process group on timeout (SIGTERM then SIGKILL). Two-layer timeout: child SIGALRM at `timeout`, parent safety net at `timeout + 60s`.
- **DB-level:** `retry_on_db_error` decorator retries transient errors (deadlocks, dropped connections, `database is locked`) with exponential backoff. `configure_connection_resilience()` sets WAL mode + `busy_timeout` (SQLite) and `statement_timeout` (Postgres). See `src/sqlery/core/db_resilience.py`.
- **Zombie detection:** Daemon periodically scans for running jobs whose worker is dead/missing using a 5-check heuristic (PID gone, no worker row, worker dead, worker moved on, heartbeat stale) and re-queues or fails them.

## Cross-Cutting Concerns

**Logging:** Module-level `logger = logging.getLogger(__name__)`; config helpers in `src/sqlery/core/log_config.py`. F-strings in log messages.

**Validation:** Light. Public API validates `task_path` is importable; backends rely on DB constraints + JSON serialisation of `kwargs`/`meta`.

**Authentication:** Dashboard auth is delegated to host framework — Django admin uses Django auth (`is_staff`), FastAPI dashboard ships unauthenticated and expects the deployer to put it behind a reverse proxy.

**Configuration:** Single source via `Config` ABC. Django path: `settings.DJANGO_SQL_JOBS` dict with defaults in `src/sqlery/django_sqlery/settings.py`. Standalone path: `StandaloneConfig` in `src/sqlery/fastapi_sqlery/config.py` overlaid with `SQLERY_*` env vars; `sqlery.compat.initialize(...)` for programmatic init.

**Webhooks:** Outgoing job-status webhooks signed via HMAC (`src/sqlery/webhooks.py`, `src/sqlery/signature.py`); migration `0010_webhooks.py` adds the schema.

**Middleware (HTTP/subprocess/daemon trigger modes):** `src/sqlery/{http_trigger,subprocess,daemon}_middleware.py` and Django mirrors at `src/sqlery/django_sqlery/*_middleware.py`.

---

*Architecture analysis: 2026-05-13*
