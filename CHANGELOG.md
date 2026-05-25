# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.21.2] - 2026-05-25

### Added

- Scheduled tasks guide and migration index rename
- Dialect-aware atomic claiming: `SELECT FOR UPDATE SKIP LOCKED` for PostgreSQL, optimistic version-CAS for SQLite

### Fixed

- Auto-register workers on-demand to fix jobs waiting with idle workers
- Dashboard no longer spams console errors when session expires
- Dashboard no longer polls `/admin/sqlery/undefined` when config is missing

## [0.21.1] - 2026-05-18

### Fixed

- Wrap `claim_job` in `transaction.atomic` to fix worker crash-loop on PostgreSQL

## [0.21.0] - 2026-05-18

v0.21 milestone: 4 phases, 137 commits, full execution-mode parity across Django and standalone.

### Breaking

- **BREAKING:** Django minimum version raised to 5.2 LTS. Users on Django 4.2 must upgrade before installing this release. Required for native async ORM features used in the async worker rebuild.

### Added — Phase 01: Core Unification

- Framework-agnostic core in `sqlery.core` — all business logic decoupled from Django
- Standalone import verification in CI (no Django required for core imports)
- Dated deprecation stubs for moved modules (`worker_claiming.py`, `executor.py`)

### Added — Phase 02: Execution Modes

- `AsyncDatabaseBackend` ABC for hot-path async methods
- `DjangoAsyncBackend` — async implementation backed by Django 5.2 ORM
- `SQLAlchemyAsyncBackend` — async implementation backed by aiosqlite/asyncpg
- `AsyncWorker` rewrite with drain-with-deadline shutdown
- `shutting_down` job status for graceful drain
- Parametrized E2E test matrix for all existing execution modes
- Django daemon `--once` flag for CI/testing

### Added — Phase 03: Testing & CI

- PostgreSQL CI matrix with marker routing (`@pytest.mark.postgres`)
- Coverage gate with `fail_under` threshold
- Real-subprocess chaos tests for zombie detection, lease lifecycle
- Hypothesis-based property test infrastructure (stubbed pending pipeline rewrite)

### Added — Phase 04: Security & Cleanup

- `ALLOWED_TASK_MODULES` allowlist for task module imports (SEC-04)
- Dashboard auth middleware: `standalone` (API key), `disabled`, or `inherit` (SEC-01)
- SSRF defense for webhook URLs — blocks private IP ranges (SEC-02)
- CSRF protection restored on 10 state-changing admin endpoints (SEC-03)
- 22 backward-compatibility stubs date-stamped with `Remove after 2027-05-14`
- Drop-in RQ/django-tasks-scheduler compatibility declared permanent first-class feature

### Fixed

- `claim_next_job_with_queue_priority` arity mismatch (found during gap-closure)
- `worker_process.py:71` arity bug — pass `(worker, backend, queues)`
- Webhooks module moved to `django_sqlery/` with dated BC stub

## [0.20.4] - 2026-03-19

### Added

- Bulk archive scheduled jobs from dashboard

## [0.20.3] - 2026-03-19

### Fixed

- Increase job output truncation limits

## [0.20.2] - 2026-03-19

### Changed

- Finish top-level import migration in `core/`

## [0.20.1] - 2026-03-18

### Added

- Daemon watchdog and intervention API
- TTL retention tests, subprocess lifecycle tests

### Fixed

- Import cleanup in core modules

## [0.20.0] - 2026-03-16

Initial release — core library with Django and FastAPI integrations.

### Added

- Database-backed job queue with PostgreSQL and SQLite support
- `@job` decorator and `enqueue()` / `enqueue_at()` API
- Django integration: ORM models, admin, management commands
- Standalone integration: SQLModel models, FastAPI dashboard, Alembic migrations
- Daemon mode with worker pool, scheduler, heartbeats, and lease management
- Subprocess (fork-per-job) execution mode
- HTTP trigger execution mode
- Lambda/serverless handler
- Synchronous thread execution mode
- RQ and django-tasks-scheduler compatibility layers
- Typer-based CLI (`sqlery`, `sqlery-worker`, `sqlery-web`, `sqlery-daemon`, etc.)
- GitHub Actions CI with stress tests
- Job dependencies, retry with exponential backoff, rate limiting
- Cron-based scheduled tasks via `croniter`
- Optimistic locking (SQLite) and `SKIP LOCKED` (PostgreSQL) for concurrent claiming
