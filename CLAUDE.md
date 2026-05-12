<!-- GSD:project-start source:PROJECT.md -->
## Project

**Sqlery — Feature-Complete Run Modes**

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and multiple execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The goal is to make every execution mode production-ready across both integration modes, with full test coverage and security hardening.

**Core Value:** Every execution mode works reliably and is tested in CI across both Django and standalone integration modes, on both SQLite and PostgreSQL.

### Constraints

- **Python version**: 3.10+ minimum (uses `X | None` union syntax)
- **Database**: PostgreSQL (production) or SQLite (dev/lightweight). No other DB engines.
- **Backward compatibility**: Public API (`@job`, `enqueue`, `Queue`) must remain stable
- **Fork safety**: Must handle DB connection lifecycle around `os.fork()` correctly
- **No new dependencies**: Prefer using existing deps (httpx, sqlmodel, asyncio) over adding new ones
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.10+ - All source code, tests, and tooling
- HTML/Jinja2 - Dashboard templates (`src/sqlery/fastapi_sqlery/templates/`, `src/sqlery/django_sqlery/templates/`)
- CSS/JS - Dashboard static assets (`src/sqlery/django_sqlery/static/`)
## Runtime
- CPython 3.10+ (minimum)
- Tested in CI against 3.11, 3.12, 3.13
- uv (primary, used in CI and Makefile)
- pip-compatible (standard `pyproject.toml` with hatchling build backend)
- Lockfile: `uv.lock` present
## Frameworks
- Django >= 4.2 - Django integration mode (ORM, admin, management commands)
- FastAPI >= 0.104.0 - Standalone mode web dashboard and REST API
- SQLModel >= 0.0.14 - Standalone mode ORM (SQLAlchemy + Pydantic)
- SQLAlchemy (transitive via SQLModel) - Database engine, session management, connection pooling
- pytest >= 7.4.0 - Test runner
- pytest-django >= 4.5.0 - Django integration tests
- pytest-asyncio >= 0.23.0 - Async test support
- pytest-cov >= 4.1.0 - Coverage reporting
- pytest-timeout >= 2.2.0 - Test timeout enforcement
- hypothesis >= 6.92.0 - Property-based and chaos testing
- hatchling - Build backend (`pyproject.toml` `[build-system]`)
- black >= 23.0.0 - Code formatter (line-length 100, target py310)
- ruff >= 0.1.0 - Linter (line-length 100, target py310)
- uv - Package management and virtual environment
## Key Dependencies
- `croniter` >= 2.0.0 - Cron expression parsing and next-occurrence calculation
- `uuid6` >= 2024.1.0 - UUID v7 generation for time-sortable worker IDs
- `psycopg` >= 3.1 - PostgreSQL adapter (psycopg3, async-capable)
- `django` >= 4.2 - Full Django framework
- `django-tasks` >= 0.1.0 - Optional async task execution backend
- `sqlmodel` >= 0.0.14 - SQLAlchemy + Pydantic ORM layer
- `fastapi` >= 0.104.0 - Web framework for dashboard/API
- `uvicorn[standard]` >= 0.24.0 - ASGI server
- `jinja2` >= 3.1.0 - Template rendering for web dashboard
- `alembic` >= 1.12.0 - Database migrations for standalone mode
- `typer` >= 0.9.0 - CLI framework
- `rich` >= 13.0.0 - Terminal formatting for CLI output
- `httpx` >= 0.24.0 - HTTP trigger mode (async HTTP client)
- `boto3` >= 1.34.0 - AWS EventBridge/Lambda trigger mode
- `requests` - Webhook delivery (referenced in `src/sqlery/webhooks.py` but not in pyproject.toml deps)
## Configuration
- Settings via `DJANGO_SQL_JOBS` dict in Django `settings.py`
- Full defaults in `src/sqlery/django_sqlery/settings.py` (`DEFAULTS` dict)
- `get_setting(name, default)` function for runtime access with self-healing fallbacks
- Migration helper: `migrate_settings()` for converting from RQ/django-tasks-scheduler config
- In-memory config via `StandaloneConfig` class (`src/sqlery/fastapi_sqlery/config.py`)
- Environment variables: `SQLERY_DATABASE_URL`, `SQLERY_POOL_SIZE`, `SQLERY_MAX_OVERFLOW`, `SQLERY_POOL_TIMEOUT`, `SQLERY_POOL_RECYCLE`, `DJANGO_SQL_JOBS_MAX_WORKERS`, `DJANGO_SQL_JOBS_ENABLE_DAEMON`, `DJANGO_SQL_JOBS_CHECK_INTERVAL`
- Programmatic init: `from sqlery.compat import initialize; initialize(database_url=..., max_workers=...)`
- `pyproject.toml` - Project metadata, dependencies, tool config (black, ruff, pytest)
- `alembic.ini` - Alembic migration configuration for standalone mode
- `Makefile` - Development automation (sample project, workers, stress tests)
## CLI Entry Points
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
- Default for development and lightweight deployments
- WAL mode and `busy_timeout` auto-configured via `connection_created` signal in Django mode (`src/sqlery/django_sqlery/apps.py`)
- Optimistic locking with version field for concurrent access (`src/sqlery/core/models.py` `QueuedJob.version`)
- `StaticPool` used for SQLAlchemy engine in standalone mode
- Production-recommended database
- `SELECT FOR UPDATE SKIP LOCKED` for atomic job claiming
- Connection pooling via SQLAlchemy `QueuePool` (`pool_size`, `max_overflow`, `pool_pre_ping`)
- Configurable `statement_timeout` and `lock_timeout` (`src/sqlery/django_sqlery/settings.py`)
- CI tests run against PostgreSQL 15
## Platform Requirements
- Python 3.10+
- uv (recommended) or pip
- SQLite (built-in) or PostgreSQL
- GNU Make (for `Makefile` targets)
- Python 3.10+
- PostgreSQL 15+ (recommended) or SQLite
- Deployment targets: bare metal, Docker, AWS Lambda (serverless mode)
- CI: GitHub Actions with ubuntu-latest
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Use `snake_case.py` for all Python modules
- Use descriptive names that reflect module purpose: `worker_claiming.py`, `daemon_runner.py`, `rate_limit_utils.py`
- Django management commands use snake_case: `run_jobs.py`, `cleanup_jobs.py`, `run_scheduled_tasks.py`
- Test files follow `test_<subject>.py` pattern: `test_models.py`, `test_queue.py`, `test_atomic_claiming.py`
- Migration files use numbered prefix: `0001_initial.py`, `0002_worker_multi_worker.py`
- Alembic migrations use date prefix: `20250101_0001_initial_schema.py`
- Use `snake_case` for all functions and methods
- Prefix private methods with single underscore: `_cleanup_stale_jobs()`, `_retry_job()`, `_spawn_next_worker()`
- Use verb-noun naming: `mark_running()`, `get_due_tasks()`, `execute_job()`, `calculate_retry_delay()`
- Boolean-returning methods use `is_`, `has_`, `can_`, `should_` prefixes: `is_alive()`, `can_execute_job()`, `should_retry()`
- Use `snake_case` for all variables and parameters
- Use `UPPER_SNAKE_CASE` for module-level constants: `STATUS_CHOICES`, `DEFAULTS`, `SCHEDULE_TYPE_CHOICES`
- Use descriptive names for query results: `queued_jobs`, `due_task_ids`, `processed_jobs`
- Use `PascalCase` for classes: `TaskExecutor`, `QueuedJob`, `ScheduledTask`, `JobFunction`, `AsyncJobFunction`
- Use `PascalCase` for custom exceptions: `ConcurrentModificationError`
- Use `PascalCase` for Django model managers: `ScheduledTaskManager`
- Backward compatibility aliases use PascalCase: `TaskExecution = QueuedJob`, `Worker = WorkerProcess`
- Django model choice lists are module-level tuples of tuples:
## Code Style
- Tool: `black` (configured in `pyproject.toml`)
- Line length: 100 characters
- Target Python version: 3.10+
- Tool: `ruff` (configured in `pyproject.toml`)
- Line length: 100 characters
- Target Python version: py310
- Use modern union syntax: `str | None` (not `Optional[str]`)
- Use `Callable`, `Any` from `typing`
- Use return type annotations on public methods:
## Import Organization
- Use relative imports within a package: `from .models import QueuedJob, ScheduledTask`
- Use absolute imports for cross-package references: `from sqlery.core.utils import calculate_next_run`
- Use lazy imports inside functions for optional dependencies or circular import prevention:
- Guard optional dependency imports with try/except:
- No path aliases configured; all imports use the standard Python module path
- Source code lives in `src/sqlery/` and is installed as the `sqlery` package via hatchling
## Error Handling
- Use custom exceptions for domain-specific errors: `ConcurrentModificationError` in `src/sqlery/django_sqlery/models.py`
- Use optimistic locking with version field for concurrent modification detection:
- Wrap external operations in try/except with logging, never crash silently:
- API views return structured JSON errors with HTTP status codes:
- Task execution captures full tracebacks:
- Background/async operations: catch, log, and continue (never crash the worker loop)
- Synchronous API calls: propagate to caller with structured error responses
- Signal handlers (SIGALRM for timeout): raise `TimeoutError` to be caught by job executor
- Database operations: use atomic transactions with rollback on failure
## Logging
- Initialize logger at module level:
- Use appropriate log levels:
- Use f-strings in log messages:
## Comments
- Module-level docstrings explain purpose: `"""Task execution engine for sqlery."""`
- Class docstrings describe responsibility and schema synchronization:
- Use `# #CLEANUP:` prefix for migration/backward-compatibility stubs that should be removed later:
- Commented-out code blocks are preserved with `# Old:` or `# #` prefix to document what was replaced
- Inline comments for non-obvious logic:
- Use Google-style docstrings with Args, Returns, Raises, Example sections:
- All public methods and classes have docstrings
- Django model fields use `help_text` parameter for documentation:
## Function Design
- Use keyword arguments with defaults for optional params:
- Use `**kwargs` for pass-through arguments in decorators and fluent APIs
- Override pattern: parameter > decorator default > system config default:
- Return the mutated object for method chaining: `execute_job()` returns the `QueuedJob`
- Return `None` for "not found" or "skipped" cases: `_enqueue_for_scheduled_task()` returns `None` if already queued
- Return lists for batch operations: `run_queue_workers()` returns `list[QueuedJob]`
- Return tuples for multi-value results: `check_dependencies_met()` returns `(bool, list)`
- Return `bool` for success/failure: `atomic_claim_job()` returns `True`/`False`
## Module Design
- Use `__all__` in `__init__.py` to define public API:
- Re-export backward compatibility aliases at package level
- When code is moved, keep a stub file that re-exports from the new location:
- This pattern is used in: `src/sqlery/models.py`, `src/sqlery/executor.py`, `src/sqlery/decorators.py`, `src/sqlery/utils.py`
- `src/sqlery/__init__.py` serves as the main barrel file, re-exporting core functionality
- Django integration exports are separate in `src/sqlery/django_sqlery/__init__.py`
## Django-Specific Conventions
- Always set explicit `db_table` name: `db_table = "sqlery_queued_job"`
- Define `ordering` in Meta for consistent query results
- Add `verbose_name` and `verbose_name_plural` for admin display
- Add database indexes for commonly queried fields:
- Use a single `DJANGO_SQL_JOBS` dict in Django settings
- All settings have defaults defined in `src/sqlery/django_sqlery/settings.py`
- Access settings via `get_setting()` which provides self-healing fallback:
- Located in `src/sqlery/django_sqlery/management/commands/`
- Use Django's `BaseCommand` class
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
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
- Abstract `DatabaseBackend` ABC with 30+ methods defines the contract
- `DjangoBackend` and `SQLAlchemyBackend` are the two concrete implementations
- `get_backend()` returns the singleton for the current mode (lazy initialization)
- All business logic (worker, scheduler, claiming) is framework-agnostic and delegates to the backend
- Fork-per-job execution model (like RQ) for memory safety
- Database-backed everything: job queue, scheduler, worker heartbeats, leases
## Layers
- Purpose: User-facing functions and decorators for enqueueing jobs
- Location: `src/sqlery/__init__.py`, `src/sqlery/core/job.py`, `src/sqlery/core/job_queue.py`
- Contains: `@job` decorator, `enqueue()`, `enqueue_at()`, `get_queue()`, `Queue` class
- Depends on: Compat layer (get_backend, get_config)
- Used by: Application code
- Purpose: Framework-agnostic business logic for job processing
- Location: `src/sqlery/core/`
- Contains: Worker, Scheduler, Claiming algorithm, Daemon, Registry, Cleanup, CLI, Utils
- Depends on: Compat layer (DatabaseBackend, Config)
- Used by: CLI, Django management commands, FastAPI app, Lambda handler
- Purpose: Auto-detects mode and provides unified interfaces
- Location: `src/sqlery/compat/__init__.py`
- Contains: `DatabaseBackend` ABC, `Config` ABC, `get_backend()`, `get_config()`, `initialize()`
- Depends on: Backend implementations (lazy import)
- Used by: All core logic, public API
- Purpose: Django-specific models, admin, management commands, middleware
- Location: `src/sqlery/django_sqlery/`
- Contains: Django models (24 migrations), DjangoBackend, admin site, dashboard views, management commands
- Depends on: Django ORM, Core logic layer (via Compat)
- Used by: Django projects
- Purpose: SQLModel models, FastAPI dashboard, Alembic migrations
- Location: `src/sqlery/fastapi_sqlery/`, `src/sqlery/core/models.py`
- Contains: SQLModel models, SQLAlchemyBackend, FastAPI app, CLI, database session management
- Depends on: SQLModel/SQLAlchemy, FastAPI, Core logic layer (via Compat)
- Used by: Non-Django Python projects
- Purpose: Drop-in replacements for RQ and django-tasks-scheduler
- Location: `src/sqlery/compat/rq.py`, `src/sqlery/compat/scheduler.py`
- Contains: RQ-compatible Queue, Retry, get_current_job; scheduler-compatible Task, TaskType
- Depends on: Django integration layer
- Used by: Projects migrating from RQ or django-tasks-scheduler
## Data Flow
### Primary Request Path: Enqueueing a Job
### Primary Execution Path: Processing a Job
### Scheduled Task Path
### Serverless / Lambda Path
- All state is in the database (PostgreSQL or SQLite). No in-memory queues.
- Worker heartbeats are DB rows updated every daemon cycle via SIGUSR1 signals.
- Queue ownership uses DB-backed leases (DaemonLease model) with TTL-based expiry.
- Optimistic locking via `version` field on QueuedJob for SQLite (CAS pattern).
- Postgres uses `SELECT FOR UPDATE SKIP LOCKED` for atomic claiming.
## Key Abstractions
- Purpose: Unified interface for all database operations across Django/SQLAlchemy
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
- Pattern: Strategy pattern with runtime auto-detection
- Purpose: Turns any function into an enqueueable task with `.enqueue()`, `.delay()`, `.enqueue_at()` methods
- Examples: `src/sqlery/core/job.py`
- Pattern: Decorator pattern preserving callable semantics (direct call still works)
- Purpose: Unified configuration interface across Django settings and standalone env vars
- Examples: `src/sqlery/compat/__init__.py` (ABC), `src/sqlery/django_sqlery/config.py`, `src/sqlery/fastapi_sqlery/config.py`
- Pattern: Strategy pattern; Django reads `settings.DJANGO_SQL_JOBS`, standalone uses in-memory dict + env vars
- Purpose: Memory-safe job execution with crash isolation
- Examples: `src/sqlery/core/worker.py`
- Pattern: Parent claims jobs and forks children. Child executes one job, writes result to DB, exits. Parent never blocked by job execution. Like RQ's execution model.
## Entry Points
- Location: `src/sqlery/__init__.py`
- Triggers: `from sqlery import enqueue, Queue, Worker, job`
- Responsibilities: Re-exports core API, conditionally imports Django decorators
- Location: `src/sqlery/core/cli.py`
- Triggers: `pyproject.toml [project.scripts]` entries: `sqlery`, `sqlery-worker`, `sqlery-web`, `sqlery-daemon`, `sqlery-jobs`, `sqlery-cleanup`, `sqlery-migrate`, `sqlery-tasks`, `sqlery-queues`
- Responsibilities: Typer-based CLI for standalone mode (daemon, workers, jobs, tasks, cleanup, migrations)
- Location: `src/sqlery/core/worker_runner.py`
- Triggers: Spawned by `WorkerPoolManager.spawn_worker()` as `python -m sqlery.core.worker_runner`
- Responsibilities: Initializes Django if needed, creates `WorkerProcess`, runs poll loop
- Location: `src/sqlery/fastapi_sqlery/app.py`
- Triggers: `sqlery-web` CLI or `uvicorn sqlery.fastapi_sqlery.app:app`
- Responsibilities: Web UI dashboard + REST API for standalone mode
- Location: `src/sqlery/django_sqlery/apps.py`
- Triggers: Adding `'sqlery.django_sqlery'` to Django `INSTALLED_APPS`
- Responsibilities: AppConfig with SQLite WAL mode signal, registers admin
- Location: `src/sqlery/django_sqlery/management/commands/`
- Triggers: `python manage.py daemon`, `python manage.py run_jobs`, `python manage.py workers`, etc.
- Responsibilities: Django-style CLI for daemon, workers, scheduler, cleanup, import/export
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
### Duplicated model definitions
## Error Handling
- **Job-level:** Try/except around task execution, mark job failed with error + traceback. Retry with exponential backoff if `max_retries > 0`.
- **Worker-level:** Unhandled errors in main loop caught, DB connections reset, sleep, continue. Worker marks status='dead' on shutdown.
- **Daemon-level:** Each daemon cycle step wrapped in try/except, errors logged but loop continues. Signal-based graceful shutdown.
- **Fork-level:** Parent kills child process group on timeout (SIGTERM then SIGKILL). Two-layer timeout: child SIGALRM at `timeout`, parent safety net at `timeout + 60s`.
- **DB-level:** `retry_on_db_error` decorator retries transient errors (deadlocks, connection drops, database locked) with exponential backoff. `configure_connection_resilience()` sets WAL mode, busy_timeout (SQLite), statement_timeout (Postgres).
- **Zombie detection:** Daemon periodically scans for running jobs with dead workers (5 checks: PID gone, no worker, worker dead, worker moved on, heartbeat stale).
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
