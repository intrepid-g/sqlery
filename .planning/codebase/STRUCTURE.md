# Codebase Structure

**Analysis Date:** 2026-05-12

## Directory Layout

```
sqlery/
├── src/sqlery/                    # Main package (installed as 'sqlery')
│   ├── __init__.py                # Public API re-exports, version
│   ├── core/                      # Framework-agnostic business logic
│   │   ├── __init__.py
│   │   ├── job.py                 # @job decorator and JobWrapper
│   │   ├── job_queue.py           # Queue class, enqueue(), claim_job()
│   │   ├── worker.py              # JobExecutor, WorkerProcess (fork-per-job)
│   │   ├── worker_pool.py         # WorkerPoolManager (spawns/monitors workers)
│   │   ├── worker_runner.py       # Entry point for spawned worker subprocesses
│   │   ├── daemon.py              # DaemonManager (main daemon loop)
│   │   ├── daemon_runner.py       # Script entry point for daemon subprocess
│   │   ├── scheduler.py           # Scheduler (enqueues jobs from cron tasks)
│   │   ├── scheduler_tasks.py     # Scheduled task utilities
│   │   ├── claiming.py            # Job claiming algorithm (tags, rate limits, deps)
│   │   ├── registry.py            # RQ-compatible job lifecycle registry
│   │   ├── cleanup.py             # Retention-based job/registry cleanup
│   │   ├── cli.py                 # Typer CLI (standalone mode)
│   │   ├── models.py              # SQLModel models (standalone mode)
│   │   ├── model_schemas.py       # Shared field/schema definitions
│   │   ├── model_utils.py         # Model utility helpers
│   │   ├── db_resilience.py       # DB retry decorator, WAL/timeout config
│   │   └── utils.py               # Shared utilities (import_task, parse_rate_limit, cron)
│   │
│   ├── compat/                    # Compatibility and abstraction layer
│   │   ├── __init__.py            # DatabaseBackend ABC, Config ABC, get_backend(), initialize()
│   │   ├── rq.py                  # RQ drop-in replacement (deprecated)
│   │   └── scheduler.py           # django-tasks-scheduler compatibility
│   │
│   ├── django_sqlery/             # Django integration (app: 'sqlery.django_sqlery')
│   │   ├── __init__.py
│   │   ├── apps.py                # DjangoSqleryConfig AppConfig
│   │   ├── models.py              # Django ORM models (QueuedJob, ScheduledTask, Worker, etc.)
│   │   ├── backend.py             # DjangoBackend (DatabaseBackend implementation)
│   │   ├── config.py              # DjangoConfig (reads settings.DJANGO_SQL_JOBS)
│   │   ├── settings.py            # Settings helper (get_setting)
│   │   ├── admin.py               # Django admin registration
│   │   ├── admin_site.py          # Custom admin site
│   │   ├── urls.py                # URL patterns (API + dashboard)
│   │   ├── views.py               # Core views (health, internal worker)
│   │   ├── api_views.py           # JSON API endpoints
│   │   ├── dashboard_views.py     # HTML dashboard views
│   │   ├── queue.py               # Django-specific Queue class
│   │   ├── decorators.py          # Django-specific @job, @async_job decorators
│   │   ├── signature.py           # Job signature support
│   │   ├── executor.py            # Django-specific task executor
│   │   ├── worker_claiming.py     # Django worker claiming (delegates to core)
│   │   ├── worker_process.py      # Django worker process helpers
│   │   ├── worker_registry.py     # Django worker registry
│   │   ├── registries.py          # Django registry wrappers
│   │   ├── cleanup.py             # Django cleanup helpers
│   │   ├── db_compat.py           # DB compatibility (SQLite vs Postgres claiming)
│   │   ├── utils.py               # Django-specific utilities
│   │   ├── friendly_name.py       # Human-readable worker names
│   │   ├── middleware.py           # Django middleware (auto-start daemon)
│   │   ├── daemon_manager.py      # Django daemon manager wrapper
│   │   ├── daemon_middleware.py    # Daemon auto-start middleware
│   │   ├── daemon_worker.py       # Django daemon worker helpers
│   │   ├── subprocess_executor.py # Subprocess-based job execution
│   │   ├── subprocess_middleware.py# Subprocess middleware
│   │   ├── http_trigger_middleware.py # HTTP trigger for job processing
│   │   ├── management/            # Django management commands
│   │   │   └── commands/
│   │   │       ├── daemon.py      # manage.py daemon start/stop/restart
│   │   │       ├── run_jobs.py    # manage.py run_jobs
│   │   │       ├── run_scheduled_tasks.py
│   │   │       ├── workers.py     # manage.py workers list/stop/kill
│   │   │       ├── cleanup_jobs.py
│   │   │       ├── rqworker.py    # RQ compatibility command
│   │   │       ├── sqlery_export.py
│   │   │       └── sqlery_import.py
│   │   ├── migrations/            # Django migrations (0001-0024)
│   │   ├── templates/             # Django admin templates
│   │   │   └── admin/sqlery/
│   │   │       ├── queuedjob/
│   │   │       └── scheduledtask/
│   │   └── static/                # Static assets (CSS, JS)
│   │       ├── admin/js/
│   │       └── sqlery/
│   │           ├── css/
│   │           └── js/
│   │
│   ├── fastapi_sqlery/            # Standalone mode (FastAPI dashboard)
│   │   ├── __init__.py
│   │   ├── app.py                 # FastAPI app (dashboard + REST API)
│   │   ├── backend.py             # SQLAlchemyBackend (DatabaseBackend impl)
│   │   ├── config.py              # StandaloneConfig (in-memory + env vars)
│   │   ├── cli.py                 # FastAPI-specific CLI (worker, web)
│   │   ├── database.py            # SQLAlchemy engine + session management
│   │   └── templates/             # Jinja2 dashboard templates
│   │
│   ├── management/                # Legacy management commands (root level)
│   │   └── commands/
│   │       ├── daemon.py
│   │       ├── run_jobs.py
│   │       ├── run_scheduled_tasks.py
│   │       ├── workers.py
│   │       ├── cleanup_jobs.py
│   │       └── replay_job.py
│   │
│   ├── templates/                 # Legacy templates (root level)
│   │   └── admin/sqlery/
│   │
│   ├── crontab.py                 # Vendored cron parser (from crontabula)
│   ├── lambda_handler.py          # AWS Lambda handler
│   ├── eventbridge_trigger.py     # AWS EventBridge integration
│   ├── triggers.py                # Task trigger utilities
│   ├── webhooks.py                # Webhook support
│   ├── decorators.py              # Root-level decorators (legacy)
│   ├── models.py                  # Root-level models (legacy)
│   ├── queue.py                   # Root-level queue (legacy)
│   ├── schema.py                  # Pydantic schemas
│   ├── tables.py                  # Table definitions
│   ├── views.py                   # Root-level views (legacy)
│   ├── urls.py                    # Root-level URLs (legacy)
│   ├── settings.py                # Root-level settings (legacy)
│   ├── admin.py                   # Root-level admin (legacy)
│   ├── apps.py                    # Root-level apps (legacy)
│   ├── middleware.py              # Root-level middleware (legacy)
│   ├── worker.py                  # Root-level worker (legacy)
│   ├── worker_pool.py             # Root-level worker pool (legacy)
│   ├── worker_claiming.py         # Root-level claiming (legacy)
│   ├── worker_process.py          # Root-level worker process (legacy)
│   ├── worker_registry.py         # Root-level worker registry (legacy)
│   ├── registries.py              # Root-level registries (legacy)
│   ├── cleanup.py                 # Root-level cleanup (legacy)
│   ├── executor.py                # Root-level executor (legacy)
│   ├── subprocess_executor.py     # Root-level subprocess executor (legacy)
│   ├── subprocess_middleware.py   # Root-level subprocess middleware (legacy)
│   ├── daemon_manager.py          # Root-level daemon manager (legacy)
│   ├── daemon_middleware.py       # Root-level daemon middleware (legacy)
│   ├── daemon_worker.py           # Root-level daemon worker (legacy)
│   ├── dashboard_views.py         # Root-level dashboard views (legacy)
│   ├── db_compat.py               # Root-level DB compat (legacy)
│   ├── http_trigger_middleware.py  # Root-level HTTP trigger (legacy)
│   ├── rate_limit_utils.py        # Root-level rate limit utils (legacy)
│   ├── signature.py               # Root-level signature (legacy)
│   ├── utils.py                   # Root-level utils (legacy)
│   └── async_queue.py             # Async queue support
│
├── alembic/                       # Alembic migrations (standalone mode)
│   └── versions/                  # Migration files
├── alembic.ini                    # Alembic configuration
│
├── tests/                         # Test suite
│   ├── settings.py                # Django test settings
│   ├── test_*.py                  # Unit/integration tests (~20 test files)
│   ├── chaos/                     # Chaos/property-based tests
│   │   ├── test_property_based.py # Hypothesis property tests
│   │   └── test_worker_chaos.py   # Worker chaos tests
│   └── integration/               # Integration tests
│
├── examples/                      # Usage examples
│   ├── basic_sync/                # Synchronous usage
│   ├── basic_async/               # Async usage
│   ├── demo_project/              # Demo project
│   └── lambda/                    # AWS Lambda example
│
├── sample_project/                # Django sample project
│   ├── mysite/                    # Django project config
│   └── tasks_app/                 # Sample tasks app
│
├── stress_test/                   # Stress/benchmark tests
│   ├── rq_app/                    # RQ comparison benchmark
│   └── sqlery_app/                # sqlery benchmark
│
├── docs/                          # Documentation
├── .github/workflows/             # GitHub Actions CI
├── .makefile-configs/             # Makefile configuration fragments
├── pyproject.toml                 # Project metadata and build config
├── uv.lock                        # Dependency lock file (uv)
├── Makefile                       # Build/dev task runner
├── CHANGELOG.md                   # Release changelog
├── CONTRIBUTING.md                # Contribution guide
└── README.md                      # Project documentation
```

## Directory Purposes

**`src/sqlery/core/`:**
- Purpose: Framework-agnostic business logic. All code here works with any backend.
- Contains: Worker, daemon, scheduler, claiming algorithm, registry, cleanup, CLI, utilities
- Key files: `worker.py` (JobExecutor + WorkerProcess), `daemon.py` (DaemonManager), `claiming.py` (job claiming algorithm), `job_queue.py` (Queue + enqueue), `job.py` (@job decorator)

**`src/sqlery/compat/`:**
- Purpose: Backend abstraction and compatibility layers
- Contains: `DatabaseBackend` ABC (30+ abstract methods), `Config` ABC, auto-detection, RQ/scheduler compat
- Key files: `__init__.py` (all ABCs and `get_backend()`/`get_config()`/`initialize()`)

**`src/sqlery/django_sqlery/`:**
- Purpose: Full Django integration -- models, admin, views, management commands
- Contains: Django ORM models with 24 migrations, DjangoBackend, admin customization, HTML dashboard, API views
- Key files: `models.py` (QueuedJob, ScheduledTask, Worker, etc.), `backend.py` (DjangoBackend), `urls.py` (routes)

**`src/sqlery/fastapi_sqlery/`:**
- Purpose: Standalone mode with FastAPI web dashboard
- Contains: SQLAlchemyBackend, FastAPI app, database session management, standalone config
- Key files: `app.py` (FastAPI routes), `backend.py` (SQLAlchemyBackend), `database.py` (engine/session)

**`tests/`:**
- Purpose: Test suite using pytest + pytest-django
- Contains: Unit tests, chaos tests (Hypothesis property-based), integration test directory
- Key files: `settings.py` (Django test config), `test_atomic_claiming.py`, `test_models.py`

**`alembic/`:**
- Purpose: Database migrations for standalone mode (SQLAlchemy)
- Contains: Alembic version files
- Key files: `alembic.ini` (root level), `alembic/versions/*.py`

## Key File Locations

**Entry Points:**
- `src/sqlery/__init__.py`: Package initialization, public API exports
- `src/sqlery/core/cli.py`: CLI entry point (`sqlery` command, Typer app)
- `src/sqlery/core/worker_runner.py`: Worker subprocess entry point
- `src/sqlery/core/daemon_runner.py`: Daemon subprocess entry point
- `src/sqlery/fastapi_sqlery/app.py`: FastAPI dashboard app
- `src/sqlery/lambda_handler.py`: AWS Lambda handler
- `src/sqlery/django_sqlery/apps.py`: Django AppConfig

**Configuration:**
- `pyproject.toml`: Build system, dependencies, scripts, tool config
- `alembic.ini`: Alembic migration config (standalone mode)
- `src/sqlery/django_sqlery/config.py`: Django config reader (settings.DJANGO_SQL_JOBS)
- `src/sqlery/fastapi_sqlery/config.py`: Standalone config (env vars + defaults)
- `src/sqlery/django_sqlery/settings.py`: Django settings helper
- `tests/settings.py`: Django test settings (DJANGO_SETTINGS_MODULE)

**Core Logic:**
- `src/sqlery/core/worker.py`: JobExecutor (job execution) + WorkerProcess (poll+fork loop)
- `src/sqlery/core/daemon.py`: DaemonManager (orchestrates scheduler + worker pool)
- `src/sqlery/core/claiming.py`: Job claiming algorithm with concurrency/rate/dep checks
- `src/sqlery/core/scheduler.py`: Scheduler (cron/interval/once task processing)
- `src/sqlery/core/job_queue.py`: Queue class and enqueue functions
- `src/sqlery/core/job.py`: @job decorator and JobWrapper
- `src/sqlery/compat/__init__.py`: DatabaseBackend ABC and mode detection

**Models:**
- `src/sqlery/django_sqlery/models.py`: Django ORM models (canonical for Django mode)
- `src/sqlery/core/models.py`: SQLModel models (canonical for standalone mode)
- `src/sqlery/core/model_schemas.py`: Shared schema definitions

**Testing:**
- `tests/settings.py`: Django settings for test runner
- `tests/test_*.py`: Standard test files
- `tests/chaos/test_property_based.py`: Hypothesis property-based tests
- `tests/chaos/test_worker_chaos.py`: Worker resilience tests

## Naming Conventions

**Files:**
- snake_case for all Python files: `job_queue.py`, `worker_pool.py`, `db_resilience.py`
- `test_` prefix for test files: `test_models.py`, `test_atomic_claiming.py`
- Django migrations numbered: `0001_initial.py` through `0024_add_timestamp_indexes.py`

**Directories:**
- snake_case for all directories: `django_sqlery`, `fastapi_sqlery`, `stress_test`
- Django convention for management commands: `management/commands/`

**Classes:**
- PascalCase: `JobWrapper`, `WorkerProcess`, `DaemonManager`, `DatabaseBackend`
- Django models: `QueuedJob`, `ScheduledTask`, `Worker`, `JobRegistry`

**Functions:**
- snake_case: `enqueue()`, `claim_job()`, `get_queue_stats()`, `calculate_next_run()`
- Private methods: underscore prefix: `_fork_and_execute()`, `_heartbeat()`, `_retry_job()`
- Module-level private: `_detect_mode()`, `_initialize_backend()`, `_trigger_worker_if_needed()`

**Database Tables:**
- All prefixed with `sqlery_`: `sqlery_queued_job`, `sqlery_scheduled_task`, `sqlery_worker`, `sqlery_registry`

**Configuration Keys:**
- UPPER_SNAKE_CASE: `DEFAULT_QUEUE`, `MAX_WORKERS_PER_NODE`, `DAEMON_CHECK_INTERVAL`
- Django: nested under `settings.DJANGO_SQL_JOBS` dict
- Standalone env vars: prefixed `SQLERY_` or `DJANGO_SQL_JOBS_`

## Where to Add New Code

**New Job Feature (e.g., new field on QueuedJob):**
1. Add field to Django model: `src/sqlery/django_sqlery/models.py`
2. Create Django migration: `python manage.py makemigrations sqlery`
3. Add field to SQLModel model: `src/sqlery/core/models.py`
4. Create Alembic migration: `alembic revision --autogenerate -m "add_field"`
5. Update `DatabaseBackend.create_job()` signature: `src/sqlery/compat/__init__.py`
6. Update both backend implementations: `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
7. Add tests: `tests/test_models.py` or new test file

**New Core Feature (framework-agnostic logic):**
- Primary code: `src/sqlery/core/` (new module or extend existing)
- Backend ABC methods if DB access needed: `src/sqlery/compat/__init__.py`
- Backend implementations: `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
- Tests: `tests/test_<feature>.py`

**New Django Management Command:**
- Implementation: `src/sqlery/django_sqlery/management/commands/<command_name>.py`
- Follow existing pattern: subclass `BaseCommand`, import backend via `get_backend()`

**New CLI Command (standalone):**
- Implementation: `src/sqlery/core/cli.py` (add to existing Typer app or sub-app)
- Follow existing pattern: `@app.command()` or `@sub_app.command()`

**New Django Admin View/API:**
- Dashboard HTML views: `src/sqlery/django_sqlery/dashboard_views.py`
- JSON API endpoints: `src/sqlery/django_sqlery/api_views.py`
- URL routing: `src/sqlery/django_sqlery/urls.py`
- Templates: `src/sqlery/django_sqlery/templates/admin/sqlery/`
- Static assets: `src/sqlery/django_sqlery/static/sqlery/`

**New FastAPI Dashboard Endpoint:**
- Routes: `src/sqlery/fastapi_sqlery/app.py`
- Templates: `src/sqlery/fastapi_sqlery/templates/`

**New Compatibility Layer:**
- Compat module: `src/sqlery/compat/<framework>.py`
- Follow `rq.py` or `scheduler.py` patterns

**Utilities:**
- Framework-agnostic: `src/sqlery/core/utils.py`
- Django-specific: `src/sqlery/django_sqlery/utils.py`

## Special Directories

**`src/sqlery/django_sqlery/migrations/`:**
- Purpose: Django database migrations (0001-0024)
- Generated: Yes (via `makemigrations`)
- Committed: Yes (required for deployment)

**`alembic/versions/`:**
- Purpose: Alembic database migrations for standalone mode
- Generated: Yes (via `alembic revision`)
- Committed: Yes (required for deployment)

**`src/sqlery/django_sqlery/static/`:**
- Purpose: CSS and JS for Django admin dashboard
- Generated: No (hand-written)
- Committed: Yes

**`src/sqlery/django_sqlery/templates/`:**
- Purpose: Django admin HTML templates
- Generated: No (hand-written)
- Committed: Yes

**`.planning/`:**
- Purpose: Planning documents and codebase analysis
- Generated: By analysis tools
- Committed: Yes

**Root-level legacy files (`src/sqlery/*.py` excluding `__init__.py`, `crontab.py`, `lambda_handler.py`, etc.):**
- Purpose: Legacy/backward-compatibility modules. Many are thin wrappers or copies of code now in `django_sqlery/` or `core/`.
- Note: Files like `src/sqlery/worker.py`, `src/sqlery/models.py`, `src/sqlery/admin.py` at the root package level appear to be legacy duplicates from before the `core/` and `django_sqlery/` refactoring. New code should go in `core/` or `django_sqlery/`, not at the package root.

---

*Structure analysis: 2026-05-12*
