# Codebase Structure

**Analysis Date:** 2026-05-13

## Directory Layout

```
sqlery/
├── src/sqlery/                         # Installed package
│   ├── __init__.py                     # Public API re-exports (@job, Queue, Worker, enqueue)
│   ├── apps.py                         # Top-level Django AppConfig stub (transitional)
│   ├── admin.py / urls.py / views.py   # Top-level Django stubs re-exporting from django_sqlery/
│   ├── api.py                          # Top-level API stub
│   ├── lambda_handler.py               # AWS Lambda entry point (Django mode)
│   ├── eventbridge_trigger.py          # EventBridge invocation helpers
│   ├── webhooks.py / signature.py      # Outgoing webhook delivery + HMAC signing
│   ├── async_queue.py / async_worker.py# Async execution mode
│   ├── *_middleware.py                 # http_trigger / subprocess / daemon trigger middleware
│   ├── *.py (worker, queue, models,    # Backward-compat stubs that re-export from core/ or
│   │   executor, decorators, utils,    # django_sqlery/; do NOT delete (dead-code policy:
│   │   daemon_manager, daemon_worker,  # comment-and-date instead).
│   │   worker_pool, worker_process,
│   │   worker_runner_*, worker_claiming,
│   │   worker_registry, registries,
│   │   tables, schema, cleanup,
│   │   db_compat, rate_limit_utils,
│   │   crontab, triggers, settings,
│   │   subprocess_executor,
│   │   subprocess_middleware,
│   │   http_trigger_middleware,
│   │   daemon_middleware, middleware,
│   │   dashboard_views)                #
│   │
│   ├── core/                           # Framework-agnostic business logic
│   │   ├── __init__.py
│   │   ├── job.py                      # @job decorator / JobWrapper
│   │   ├── job_queue.py                # Queue + enqueue / enqueue_at / cancel_job
│   │   ├── worker.py                   # WorkerProcess + JobExecutor (fork-per-job)
│   │   ├── worker_pool.py              # WorkerPoolManager (subprocess pool)
│   │   ├── worker_runner.py            # `python -m sqlery.core.worker_runner` entry
│   │   ├── daemon.py                   # DaemonManager (top-level orchestrator)
│   │   ├── daemon_runner.py            # Daemon subprocess runner
│   │   ├── scheduler.py                # Scheduler tick (cron / interval)
│   │   ├── scheduler_tasks.py          # ScheduledTask helpers (croniter)
│   │   ├── claiming.py                 # Claiming algorithm (tags, rate limits, deps)
│   │   ├── registry.py                 # RQ-compatible registries
│   │   ├── cleanup.py                  # Retention-based cleanup
│   │   ├── cli.py                      # Typer CLI (sqlery, sqlery-daemon, …)
│   │   ├── db_resilience.py            # retry_on_db_error, WAL/timeout config
│   │   ├── log_config.py               # Logging helpers
│   │   ├── models.py                   # Shared / SQLModel job + worker models
│   │   ├── model_schemas.py            # Pydantic schemas
│   │   ├── model_utils.py              # Model helpers
│   │   └── utils.py                    # Misc utilities (calculate_next_run, …)
│   │
│   ├── compat/                         # Mode-detection + compatibility shims
│   │   ├── __init__.py                 # DatabaseBackend ABC + Config ABC + initialize()
│   │   ├── rq.py                       # rq.Queue / Retry / get_current_job drop-in
│   │   └── scheduler.py                # django-tasks-scheduler drop-in (Task, TaskType)
│   │
│   ├── django_sqlery/                  # Django integration
│   │   ├── __init__.py
│   │   ├── apps.py                     # AppConfig (SQLite WAL signal + admin register)
│   │   ├── models.py                   # QueuedJob, ScheduledTask, Worker, DaemonLease, …
│   │   ├── backend.py                  # DjangoBackend (DatabaseBackend impl)
│   │   ├── config.py                   # DjangoConfig (reads DJANGO_SQL_JOBS)
│   │   ├── settings.py                 # DEFAULTS + get_setting()
│   │   ├── queue.py                    # Django-flavoured Queue
│   │   ├── admin.py / admin_site.py    # Admin classes + custom AdminSite
│   │   ├── dashboard_views.py          # Custom dashboard inside admin
│   │   ├── api_views.py / views.py     # JSON API + HTML views
│   │   ├── urls.py                     # Django URL routes for dashboard
│   │   ├── intervention.py             # Manual job intervention endpoints
│   │   ├── deadlines.py                # Deadline / SLA helpers
│   │   ├── friendly_name.py            # Human-readable name generator (workers)
│   │   ├── daemon_manager.py / daemon_worker.py
│   │   ├── worker_process.py / worker_registry.py / worker_claiming.py
│   │   ├── executor.py / cleanup.py / registries.py / utils.py / db_compat.py
│   │   ├── signature.py                # Webhook HMAC signing
│   │   ├── *_middleware.py             # http_trigger / subprocess / daemon middleware
│   │   ├── migrations/                 # 25 numbered Django migrations
│   │   │   ├── 0001_initial.py
│   │   │   └── … 0025_daemoncommand.py
│   │   ├── management/commands/        # Django CLI: daemon, run_jobs, workers,
│   │   │                               # cleanup_jobs, run_scheduled_tasks, rqworker,
│   │   │                               # sqlery_import, sqlery_export
│   │   ├── templates/admin/sqlery/     # Admin templates (queuedjob, scheduledtask)
│   │   └── static/{admin/js,sqlery/{css,js}}
│   │
│   ├── fastapi_sqlery/                 # Standalone integration
│   │   ├── __init__.py
│   │   ├── app.py                      # FastAPI app (dashboard + REST API)
│   │   ├── backend.py                  # SQLAlchemyBackend (DatabaseBackend impl)
│   │   ├── config.py                   # StandaloneConfig + env-var loader
│   │   ├── database.py                 # Global engine + session factory
│   │   ├── cli.py                      # sqlery-worker / sqlery-web shims
│   │   └── templates/                  # Jinja2 templates
│   │
│   ├── management/commands/            # Transitional copy: cleanup_jobs, daemon,
│   │                                   # replay_job, run_jobs, run_scheduled_tasks,
│   │                                   # workers (kept for backward compat)
│   └── templates/admin/sqlery/         # Transitional top-level admin templates
│
├── alembic/                            # Standalone Alembic migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/                       # 13 revisions (20250101_0001 … _0013)
├── alembic.ini
│
├── tests/                              # pytest suite
│   ├── __init__.py
│   ├── settings.py                     # Django test settings
│   ├── Dockerfile / compose.yml        # Postgres CI fixture
│   ├── test_*.py                       # 23 unit/integration test modules
│   ├── integration/                    # End-to-end / cross-mode integration tests
│   └── chaos/                          # hypothesis-based chaos / property tests
│
├── sample_project/                     # Django sample app for manual testing
├── stress_test/                        # Load/stress harnesses
├── examples/                           # Usage examples
├── docs/                               # Project documentation
│
├── pyproject.toml                      # Project metadata + tool config
├── uv.lock                             # uv lockfile
├── Makefile                            # Dev automation (sample, workers, stress)
├── README.md / CHANGELOG.md / CONTRIBUTING.md / LICENSE
├── migrate_django_files.sh             # One-off file-move helper
└── verify-docker-build.sh              # CI helper for Docker image
```

## Directory Purposes

**`src/sqlery/`** (top level):
- Purpose: Installed Python package. Top-level `.py` files are mostly backward-compat stubs that re-export from `core/` or `django_sqlery/`.
- Key files: `__init__.py` (public API), `lambda_handler.py`, `webhooks.py`, `async_queue.py`
- Convention: Do not delete stub files — comment-and-date if obsolete (project dead-code policy).

**`src/sqlery/core/`:**
- Purpose: Framework-agnostic business logic. The "engine" of sqlery.
- Contains: `WorkerProcess`, `JobExecutor`, `DaemonManager`, `Scheduler`, claiming algorithm, registries, cleanup, CLI, DB resilience.
- Key files: `worker.py`, `daemon.py`, `claiming.py`, `cli.py`, `job_queue.py`

**`src/sqlery/compat/`:**
- Purpose: Mode auto-detection + abstract backend/config interfaces + RQ / scheduler drop-ins.
- Key files: `__init__.py` (`DatabaseBackend`, `Config`, `initialize`, `get_backend`, `get_config`), `rq.py`, `scheduler.py`.

**`src/sqlery/django_sqlery/`:**
- Purpose: Django integration — ORM models, admin, dashboard, mgmt commands, migrations, middleware variants.
- Subdirs: `migrations/` (25 Django migrations), `management/commands/` (CLI), `templates/admin/sqlery/`, `static/`.

**`src/sqlery/fastapi_sqlery/`:**
- Purpose: Standalone (SQLModel + FastAPI) integration — backend, config, app, database engine, CLI shim.
- Subdirs: `templates/` (Jinja2 dashboard templates).

**`src/sqlery/management/commands/`:**
- Purpose: Transitional copy of Django management commands at the top-level app path. Kept for backward compatibility with projects that registered `sqlery` (not `sqlery.django_sqlery`) in `INSTALLED_APPS`.

**`alembic/`:**
- Purpose: Alembic migration environment for standalone mode. Numbered date-prefixed revisions in `alembic/versions/`.

**`tests/`:**
- Purpose: pytest suite covering both integration modes against SQLite and PostgreSQL.
- Layout: 23 `test_*.py` modules at the root, plus `integration/` and `chaos/` subdirs. `settings.py` is the Django test settings module; `compose.yml` + `Dockerfile` start a Postgres test fixture.

**`sample_project/`** and **`stress_test/`** and **`examples/`:**
- Purpose: Out-of-tree consumers used for manual testing, load testing, and documentation. Not installed.

## Key File Locations

**Entry points:**
- `src/sqlery/__init__.py` — Public API re-exports
- `src/sqlery/core/cli.py` — Typer CLI (`sqlery`, `sqlery-daemon`, `sqlery-jobs`, …)
- `src/sqlery/core/worker_runner.py` — `python -m sqlery.core.worker_runner`
- `src/sqlery/fastapi_sqlery/app.py` — FastAPI ASGI app
- `src/sqlery/lambda_handler.py` — Lambda handler
- `src/sqlery/django_sqlery/apps.py` — Django AppConfig

**Configuration:**
- `pyproject.toml` — Project metadata, deps, `[project.scripts]`, black/ruff/pytest config
- `alembic.ini` — Alembic config
- `src/sqlery/django_sqlery/settings.py` — Django defaults + `get_setting()`
- `src/sqlery/fastapi_sqlery/config.py` — `StandaloneConfig`
- `tests/settings.py` — Django test settings

**Core logic:**
- `src/sqlery/core/worker.py` — `WorkerProcess`, `JobExecutor`, fork-per-job
- `src/sqlery/core/daemon.py` — `DaemonManager` cycle
- `src/sqlery/core/claiming.py` — Atomic claim + tag/rate-limit/dep enforcement
- `src/sqlery/core/scheduler.py` — Scheduled-task tick
- `src/sqlery/compat/__init__.py` — `DatabaseBackend` ABC + mode detection
- `src/sqlery/django_sqlery/backend.py` — Django ORM backend
- `src/sqlery/fastapi_sqlery/backend.py` — SQLAlchemy backend

**Testing:**
- `tests/` (root pytest suite), `tests/integration/`, `tests/chaos/`
- `tests/Dockerfile`, `tests/compose.yml` — Postgres CI fixture

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules
- Descriptive names reflecting purpose: `worker_claiming.py`, `daemon_runner.py`, `rate_limit_utils.py`
- Test modules: `test_<subject>.py` (e.g. `test_atomic_claiming.py`, `test_version_locking.py`)
- Django migrations: numbered 4-digit prefix `0001_initial.py` … `0025_daemoncommand.py`
- Alembic migrations: date+seq prefix `20250101_0001_initial_schema.py`
- Django management commands: snake_case verbs (`run_jobs.py`, `cleanup_jobs.py`, `run_scheduled_tasks.py`)

**Directories:**
- snake_case package names (`django_sqlery`, `fastapi_sqlery`)
- Django-required layouts preserved: `management/commands/`, `migrations/`, `templates/admin/<app>/<model>/`, `static/<app>/{css,js}`

## Where to Add New Code

**New feature spanning both modes:**
- Core logic: `src/sqlery/core/<feature>.py` (no Django or SQLAlchemy imports)
- ABC additions: extend `DatabaseBackend` in `src/sqlery/compat/__init__.py`
- Django implementation: `src/sqlery/django_sqlery/backend.py` + new Django migration in `src/sqlery/django_sqlery/migrations/`
- Standalone implementation: `src/sqlery/fastapi_sqlery/backend.py` + new Alembic revision in `alembic/versions/`
- Tests: `tests/test_<feature>.py` (parametrise over both backends where practical)

**New Django-only feature:**
- Code: `src/sqlery/django_sqlery/<feature>.py`
- Admin/dashboard: extend `src/sqlery/django_sqlery/admin.py` or `dashboard_views.py`
- CLI: add a management command in `src/sqlery/django_sqlery/management/commands/`
- Migration: next numbered file in `src/sqlery/django_sqlery/migrations/`

**New standalone-only feature:**
- Code: `src/sqlery/fastapi_sqlery/<feature>.py`
- Dashboard: extend `src/sqlery/fastapi_sqlery/app.py` (route) + template in `fastapi_sqlery/templates/`
- CLI: extend `src/sqlery/core/cli.py` or `src/sqlery/fastapi_sqlery/cli.py`
- Migration: new revision in `alembic/versions/`

**New utility:**
- Cross-mode helper: `src/sqlery/core/utils.py` or a new module under `core/`
- Django-only helper: `src/sqlery/django_sqlery/utils.py`

**New test:**
- Unit: `tests/test_<subject>.py`
- Integration / cross-mode: `tests/integration/`
- Property / chaos: `tests/chaos/`

## Special Directories

**`src/sqlery/django_sqlery/migrations/`:**
- Purpose: Django migrations (25 numbered revisions).
- Generated: Yes (`python manage.py makemigrations`).
- Committed: Yes — required for upgrades.

**`alembic/versions/`:**
- Purpose: Standalone-mode schema migrations (13 revisions).
- Generated: Yes (`alembic revision --autogenerate`).
- Committed: Yes.

**`src/sqlery/django_sqlery/static/`** and **`src/sqlery/templates/admin/sqlery/`:**
- Purpose: Admin static assets and templates.
- Generated: No (hand-authored).
- Committed: Yes.

**Top-level stub modules in `src/sqlery/`:**
- Purpose: Backward-compat re-exports (e.g. `worker.py`, `queue.py`, `models.py`, `executor.py`).
- Generated: No.
- Committed: Yes — do not delete; comment-and-date if obsolete per project policy.

**`src/sqlery/management/commands/`:**
- Purpose: Transitional duplicate of Django management commands at the legacy top-level app path.
- Generated: No.
- Committed: Yes — kept for backward compatibility.

---

*Structure analysis: 2026-05-13*
