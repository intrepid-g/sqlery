# Architecture

**Analysis Date:** 2026-05-12

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│                     User Application                         │
│  @job decorator, .enqueue(), .delay(), .enqueue_at()         │
│  `src/sqlery/core/job.py`                                    │
├──────────────────┬──────────────────┬───────────────────────┤
│   Job Queue      │   Scheduler      │    CLI / Dashboard    │
│ `core/job_queue` │ `core/scheduler` │ `core/cli`, `fastapi` │
└────────┬─────────┴────────┬─────────┴──────────┬────────────┘
         │                  │                     │
         ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│             Compatibility / Backend Abstraction               │
│  `src/sqlery/compat/__init__.py`                             │
│  DatabaseBackend ABC, Config ABC, auto-detection             │
├──────────────────────────┬──────────────────────────────────┤
│    Django Backend         │    SQLAlchemy Backend            │
│  `django_sqlery/backend`  │  `fastapi_sqlery/backend`       │
│  Django ORM + migrations  │  SQLModel + Alembic             │
└──────────────────────────┴──────────────────────────────────┘
         │                           │
         ▼                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Database (PostgreSQL or SQLite)                             │
│  Tables: sqlery_queued_job, sqlery_scheduled_task,           │
│          sqlery_worker, sqlery_registry                       │
└─────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| JobWrapper / @job | Decorator that wraps functions as enqueueable jobs | `src/sqlery/core/job.py` |
| Queue / enqueue | Enqueue jobs for execution via the active backend | `src/sqlery/core/job_queue.py` |
| DatabaseBackend ABC | Abstract interface for all DB operations (30+ methods) | `src/sqlery/compat/__init__.py` |
| DjangoBackend | Django ORM implementation of DatabaseBackend | `src/sqlery/django_sqlery/backend.py` |
| SQLAlchemyBackend | SQLModel/SQLAlchemy implementation of DatabaseBackend | `src/sqlery/fastapi_sqlery/backend.py` |
| DjangoConfig | Reads config from Django settings.DJANGO_SQL_JOBS | `src/sqlery/django_sqlery/config.py` |
| StandaloneConfig | In-memory config with env var loading | `src/sqlery/fastapi_sqlery/config.py` |
| JobExecutor | Executes a single job with retry, timeout, crash recovery | `src/sqlery/core/worker.py` |
| WorkerProcess | Persistent worker that polls, forks children, monitors | `src/sqlery/core/worker.py` |
| WorkerPoolManager | Manages pool of worker subprocesses | `src/sqlery/core/worker_pool.py` |
| DaemonManager | Top-level daemon: scheduler, worker pool, heartbeats, leases | `src/sqlery/core/daemon.py` |
| Scheduler | Finds due scheduled tasks, enqueues jobs | `src/sqlery/core/scheduler.py` |
| Claiming Algorithm | Tag concurrency, rate limits, deps, atomic claim | `src/sqlery/core/claiming.py` |
| RegistryManager | RQ-compatible job lifecycle tracking | `src/sqlery/core/registry.py` |
| CleanupManager | Retention-based job/registry cleanup | `src/sqlery/core/cleanup.py` |
| CLI | Typer-based CLI for standalone mode | `src/sqlery/core/cli.py` |
| FastAPI Dashboard | Web UI + REST API for standalone mode | `src/sqlery/fastapi_sqlery/app.py` |
| Django Admin | Django admin integration + custom dashboard | `src/sqlery/django_sqlery/admin.py` |
| Lambda Handler | AWS Lambda entry point for serverless | `src/sqlery/lambda_handler.py` |
| DB Resilience | Retry decorator, WAL/timeout config | `src/sqlery/core/db_resilience.py` |

## Pattern Overview

**Overall:** Backend Abstraction / Strategy Pattern

The library provides a single public API (enqueue, claim, schedule) that works identically in two modes: **Django mode** (Django ORM) and **Standalone mode** (SQLModel/SQLAlchemy + FastAPI). Mode is auto-detected at runtime by checking if Django is configured.

**Key Characteristics:**
- Abstract `DatabaseBackend` ABC with 30+ methods defines the contract
- `DjangoBackend` and `SQLAlchemyBackend` are the two concrete implementations
- `get_backend()` returns the singleton for the current mode (lazy initialization)
- All business logic (worker, scheduler, claiming) is framework-agnostic and delegates to the backend
- Fork-per-job execution model (like RQ) for memory safety
- Database-backed everything: job queue, scheduler, worker heartbeats, leases

## Layers

**Public API Layer:**
- Purpose: User-facing functions and decorators for enqueueing jobs
- Location: `src/sqlery/__init__.py`, `src/sqlery/core/job.py`, `src/sqlery/core/job_queue.py`
- Contains: `@job` decorator, `enqueue()`, `enqueue_at()`, `get_queue()`, `Queue` class
- Depends on: Compat layer (get_backend, get_config)
- Used by: Application code

**Core Logic Layer:**
- Purpose: Framework-agnostic business logic for job processing
- Location: `src/sqlery/core/`
- Contains: Worker, Scheduler, Claiming algorithm, Daemon, Registry, Cleanup, CLI, Utils
- Depends on: Compat layer (DatabaseBackend, Config)
- Used by: CLI, Django management commands, FastAPI app, Lambda handler

**Compat / Backend Abstraction Layer:**
- Purpose: Auto-detects mode and provides unified interfaces
- Location: `src/sqlery/compat/__init__.py`
- Contains: `DatabaseBackend` ABC, `Config` ABC, `get_backend()`, `get_config()`, `initialize()`
- Depends on: Backend implementations (lazy import)
- Used by: All core logic, public API

**Django Integration Layer:**
- Purpose: Django-specific models, admin, management commands, middleware
- Location: `src/sqlery/django_sqlery/`
- Contains: Django models (24 migrations), DjangoBackend, admin site, dashboard views, management commands
- Depends on: Django ORM, Core logic layer (via Compat)
- Used by: Django projects

**Standalone Integration Layer:**
- Purpose: SQLModel models, FastAPI dashboard, Alembic migrations
- Location: `src/sqlery/fastapi_sqlery/`, `src/sqlery/core/models.py`
- Contains: SQLModel models, SQLAlchemyBackend, FastAPI app, CLI, database session management
- Depends on: SQLModel/SQLAlchemy, FastAPI, Core logic layer (via Compat)
- Used by: Non-Django Python projects

**Compatibility / Migration Layer:**
- Purpose: Drop-in replacements for RQ and django-tasks-scheduler
- Location: `src/sqlery/compat/rq.py`, `src/sqlery/compat/scheduler.py`
- Contains: RQ-compatible Queue, Retry, get_current_job; scheduler-compatible Task, TaskType
- Depends on: Django integration layer
- Used by: Projects migrating from RQ or django-tasks-scheduler

## Data Flow

### Primary Request Path: Enqueueing a Job

1. User calls `my_task.enqueue(arg=value)` or `enqueue('myapp.tasks.my_task', arg=value)` (`src/sqlery/core/job.py:60`, `src/sqlery/core/job_queue.py:131`)
2. `job_queue.enqueue()` calls `get_backend()` to get the active DatabaseBackend (`src/sqlery/compat/__init__.py:733`)
3. Backend auto-detects Django vs standalone and returns `DjangoBackend` or `SQLAlchemyBackend` (`src/sqlery/compat/__init__.py:691`)
4. `backend.create_job()` inserts a row into `sqlery_queued_job` with status='queued' (`src/sqlery/django_sqlery/backend.py:37` or `src/sqlery/fastapi_sqlery/backend.py:41`)
5. Returns the created job object to the caller

### Primary Execution Path: Processing a Job

1. **Daemon loop** polls every `DAEMON_CHECK_INTERVAL` seconds (`src/sqlery/core/daemon.py:362`)
2. Daemon runs `Scheduler.run_due_tasks()` for owned queues (`src/sqlery/core/scheduler.py:26`)
3. Daemon calls `WorkerPoolManager.ensure_workers()` to spawn worker subprocesses (`src/sqlery/core/worker_pool.py:266`)
4. **Worker subprocess** starts via `worker_runner.py`, enters `WorkerProcess.run()` poll loop (`src/sqlery/core/worker_runner.py:14`, `src/sqlery/core/worker.py:413`)
5. Worker calls `backend.claim_job(queues, worker_id)` using SELECT FOR UPDATE SKIP LOCKED (`src/sqlery/core/worker.py:468`)
6. Claiming algorithm checks tag concurrency, rate limits, dependencies, then atomically claims (`src/sqlery/core/claiming.py:156`)
7. Worker **forks** a child process via `_fork_and_execute()` (`src/sqlery/core/worker.py:548`)
8. **Child process** calls `JobExecutor.execute_job_in_child()` (`src/sqlery/core/worker.py:100`)
9. Child imports and calls the task function, writes result to DB, calls `os._exit()` (`src/sqlery/core/worker.py:141-169`)
10. **Parent process** waits via `waitpid()`, reads final status from DB, loops back to step 5 (`src/sqlery/core/worker.py:610-680`)

### Scheduled Task Path

1. Admin creates `ScheduledTask` with cron expression, queue, priority
2. Daemon's `Scheduler.run_due_tasks()` queries for tasks where `next_run_at <= now` (`src/sqlery/core/scheduler.py:43`)
3. For each due task, scheduler checks for existing pending jobs (dedup) (`src/sqlery/core/scheduler.py:75`)
4. Creates a `QueuedJob` linked to the `ScheduledTask` via `scheduled_task_id` (`src/sqlery/core/scheduler.py:86`)
5. Updates `next_run_at` based on schedule type (cron, interval, once) (`src/sqlery/core/scheduler.py:103`)
6. Job enters the normal execution path (claimed by a worker)

### Serverless / Lambda Path

1. EventBridge invokes `handler()` with action payload (`src/sqlery/lambda_handler.py:62`)
2. Handler calls `setup_django()` to initialize Django in Lambda (`src/sqlery/lambda_handler.py:99`)
3. For `process_queue`: finds and executes a job inline (`src/sqlery/lambda_handler.py:112`)
4. For `run_scheduled_task`: enqueues job and invokes another Lambda worker (`src/sqlery/lambda_handler.py:193`)
5. After processing, recursively invokes another Lambda if more jobs exist

**State Management:**
- All state is in the database (PostgreSQL or SQLite). No in-memory queues.
- Worker heartbeats are DB rows updated every daemon cycle via SIGUSR1 signals.
- Queue ownership uses DB-backed leases (DaemonLease model) with TTL-based expiry.
- Optimistic locking via `version` field on QueuedJob for SQLite (CAS pattern).
- Postgres uses `SELECT FOR UPDATE SKIP LOCKED` for atomic claiming.

## Key Abstractions

**DatabaseBackend:**
- Purpose: Unified interface for all database operations across Django/SQLAlchemy
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
- Pattern: Strategy pattern with runtime auto-detection

**JobWrapper / @job Decorator:**
- Purpose: Turns any function into an enqueueable task with `.enqueue()`, `.delay()`, `.enqueue_at()` methods
- Examples: `src/sqlery/core/job.py`
- Pattern: Decorator pattern preserving callable semantics (direct call still works)

**Config:**
- Purpose: Unified configuration interface across Django settings and standalone env vars
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/config.py`, `src/sqlery/fastapi_sqlery/config.py`
- Pattern: Strategy pattern; Django reads `settings.DJANGO_SQL_JOBS`, standalone uses in-memory dict + env vars

**Worker / Fork-per-Job Model:**
- Purpose: Memory-safe job execution with crash isolation
- Examples: `src/sqlery/core/worker.py`
- Pattern: Parent claims jobs and forks children. Child executes one job, writes result to DB, exits. Parent never blocked by job execution. Like RQ's execution model.

## Entry Points

**Python Package (`import sqlery`):**
- Location: `src/sqlery/__init__.py`
- Triggers: `from sqlery import enqueue, Queue, Worker, job`
- Responsibilities: Re-exports core API, conditionally imports Django decorators

**CLI (`sqlery` command):**
- Location: `src/sqlery/core/cli.py`
- Triggers: `pyproject.toml [project.scripts]` entries: `sqlery`, `sqlery-worker`, `sqlery-web`, `sqlery-daemon`, `sqlery-jobs`, `sqlery-cleanup`, `sqlery-migrate`, `sqlery-tasks`, `sqlery-queues`
- Responsibilities: Typer-based CLI for standalone mode (daemon, workers, jobs, tasks, cleanup, migrations)

**Worker Runner:**
- Location: `src/sqlery/core/worker_runner.py`
- Triggers: Spawned by `WorkerPoolManager.spawn_worker()` as `python -m sqlery.core.worker_runner`
- Responsibilities: Initializes Django if needed, creates `WorkerProcess`, runs poll loop

**FastAPI Dashboard:**
- Location: `src/sqlery/fastapi_sqlery/app.py`
- Triggers: `sqlery-web` CLI or `uvicorn sqlery.fastapi_sqlery.app:app`
- Responsibilities: Web UI dashboard + REST API for standalone mode

**Django App:**
- Location: `src/sqlery/django_sqlery/apps.py`
- Triggers: Adding `'sqlery.django_sqlery'` to Django `INSTALLED_APPS`
- Responsibilities: AppConfig with SQLite WAL mode signal, registers admin

**Django Management Commands:**
- Location: `src/sqlery/django_sqlery/management/commands/`
- Triggers: `python manage.py daemon`, `python manage.py run_jobs`, `python manage.py workers`, etc.
- Responsibilities: Django-style CLI for daemon, workers, scheduler, cleanup, import/export

**Lambda Handler:**
- Location: `src/sqlery/lambda_handler.py`
- Triggers: AWS Lambda invocation from EventBridge
- Responsibilities: Serverless job processing (Django mode only)

## Architectural Constraints

- **Threading:** Single-threaded event loop per worker. Workers fork child processes for job execution. No threading within workers. SIGUSR1 signal handler sets a flag (no DB calls in signal handlers to avoid corrupting psycopg connections).
- **Global state:** Singleton `_backend` and `_config` in `src/sqlery/compat/__init__.py` (module-level, initialized once per process). Global `_engine` in `src/sqlery/fastapi_sqlery/database.py`.
- **Circular imports:** Carefully managed with lazy imports. Django decorators imported conditionally in `__init__.py`. Compat layer uses absolute imports (`from sqlery.django_sqlery.backend`) instead of relative to avoid resolution in the wrong package.
- **Fork safety:** DB connections must be closed before `os.fork()` and reopened in both parent and child. `_reset_db_connections()` calls `django.db.connections.close_all()`. Child process calls `os.setpgrp()` for process group isolation.
- **SQLite limitations:** No `SELECT FOR UPDATE SKIP LOCKED` -- uses optimistic locking with version field (CAS). WAL mode enabled for concurrent reads. `busy_timeout` pragma set to 5000ms. Not recommended for production multi-worker setups.
- **Signal handling:** SIGTERM/SIGINT for graceful shutdown (forwarded to child process groups via `os.killpg`). SIGUSR1 for heartbeat requests from daemon to workers. SIGALRM for job timeout in child processes.

## Anti-Patterns

### Importing Django models at module level in core code

**What happens:** Core modules use lazy imports (`from ..django_sqlery.models import QueuedJob` inside methods) to avoid Django dependency at import time, but some places like `_fail_zombie_running_jobs` in `src/sqlery/core/daemon.py:509` directly import Django models.
**Why it's wrong:** Breaks standalone mode if these code paths are reached without Django configured.
**Do this instead:** Always delegate to `self.backend` methods. The backend abstracts away the ORM. See `src/sqlery/core/claiming.py` for the correct pattern.

### Duplicated model definitions

**What happens:** Django models in `src/sqlery/django_sqlery/models.py` and SQLModel models in `src/sqlery/core/models.py` define the same tables independently.
**Why it's wrong:** Schema drift between the two model sets requires manual synchronization.
**Do this instead:** Reference `src/sqlery/core/model_schemas.py` for shared field definitions. Always update both models when changing schema.

## Error Handling

**Strategy:** Multi-layer defense with graceful degradation

**Patterns:**
- **Job-level:** Try/except around task execution, mark job failed with error + traceback. Retry with exponential backoff if `max_retries > 0`.
- **Worker-level:** Unhandled errors in main loop caught, DB connections reset, sleep, continue. Worker marks status='dead' on shutdown.
- **Daemon-level:** Each daemon cycle step wrapped in try/except, errors logged but loop continues. Signal-based graceful shutdown.
- **Fork-level:** Parent kills child process group on timeout (SIGTERM then SIGKILL). Two-layer timeout: child SIGALRM at `timeout`, parent safety net at `timeout + 60s`.
- **DB-level:** `retry_on_db_error` decorator retries transient errors (deadlocks, connection drops, database locked) with exponential backoff. `configure_connection_resilience()` sets WAL mode, busy_timeout (SQLite), statement_timeout (Postgres).
- **Zombie detection:** Daemon periodically scans for running jobs with dead workers (5 checks: PID gone, no worker, worker dead, worker moved on, heartbeat stale).

## Cross-Cutting Concerns

**Logging:** Standard `logging` module throughout. Each module creates `logger = logging.getLogger(__name__)`. Worker stderr goes to daemon's stderr (visible in container logs). Worker stdout goes to log files in `tmp/`.

**Validation:** Cron expressions validated via vendored crontabula in `src/sqlery/crontab.py`. Rate limits parsed and validated in `src/sqlery/core/utils.py`. Pydantic models validate API requests in FastAPI mode.

**Authentication:** No built-in auth. Django mode relies on Django admin auth. FastAPI dashboard has no auth (intended for internal use). Lambda handler assumes IAM-based access.

**Configuration:** Django mode reads `settings.DJANGO_SQL_JOBS` dict. Standalone mode reads env vars (`SQLERY_DATABASE_URL`, `SQLERY_POOL_SIZE`, etc.) and supports programmatic `initialize()`. Config keys documented in `src/sqlery/fastapi_sqlery/config.py:18-54`.

---

*Architecture analysis: 2026-05-12*
