# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [0.24.9] - 2026-08-10

### Fixed

- Job-completion fencing: workers now pass the post-claim `expected_version` to `mark_job_success`/`mark_job_failed`, so a stale worker whose job was reclaimed by the zombie sweep can no longer clobber the reclaimed job's outcome. A rejected late write raises `JobFencingError`, which is logged and discarded instead of double-recording a result. (Note: the SQLAlchemy backend's check is read-then-commit, not atomic CAS — tracked in #17.)
- Async worker retry-requeue: `arequeue_retry` is now implemented on both async backends. Previously a hard-coded `isinstance` check made retries a silent no-op on the Django async backend.
- Unawaited-coroutine guard: `mark_success`/`amark_success` on both backends now reject a coroutine object as a job result instead of persisting its repr.
- `select_for_update` call sites in the Django backend are guarded by `assert_in_atomic_block`, catching lock-less claims at the seam instead of failing silently on Postgres. The SQLite no-op claim path is exempt.

## [0.24.7] - 2026-07-14

### Fixed

- `JobExecutor._kill_worker_process()` (used by the admin "Stop Job" action) never reaped the killed child, leaking it as a permanent zombie under a PID-1 worker container. Its `os.kill(pid, 0)` liveness check couldn't tell a live process from an un-reaped zombie (both answer signal-0 successfully), so termination was never actually observed. Liveness now goes through `os.waitpid(pid, os.WNOHANG)`, which detects exit and reaps in the same call.

## [0.24.5] - 2026-07-08

### Fixed

- `QueuedJob.parent_job_id` widened from `IntegerField` (int4) to `BigIntegerField` (int8). It stores a 64-bit `_generate_job_id()` value, so when a failed job spawned a retry the INSERT raised `integer out of range` — swallowed by the worker's mark-failed handler, silently preventing retries. Postgres migration `0033` runs the real `ALTER COLUMN … TYPE bigint` (cascades across partitions); SQLite is state-only. SQLModel `parent_job_id` aligned to `BigInteger` for parity (the fastapi raw DDL was already `BIGINT`).

## [0.22.0] - 2026-05-25

### Added

- RQ compatibility layer (`sqlery.compat.rq`) now works in **standalone mode** — Django is no longer required to import or use it. The `Queue` wrapper, utility functions, and `Job`/`Worker` stubs route through the framework-agnostic `DatabaseBackend` ABC via `get_backend()`, with the Django fast-path preserved. Closes the strategic gap that left standalone RQ migrants without a migration path.
- `refresh_worker_heartbeat()` on the SQLAlchemy (standalone) backend — previously missing, leaving standalone worker heartbeats un-refreshed.
- IP/origin allowlist for internal trigger endpoints (`INTERNAL_ALLOWED_IPS` for Django, `SQLERY_INTERNAL_ALLOWED_IPS` for standalone), defense-in-depth on top of the existing HMAC check. Matches the real socket peer (`REMOTE_ADDR` / `request.client.host`), never `X-Forwarded-For`; loopback-only by default.

### Changed

- Daemon zombie detection is now **mode-agnostic**: the five-heuristic liveness logic lives once in `sqlery.core` and consumes structured data via two new backend methods (`get_running_jobs_for_liveness`, `fail_zombie_job`). Standalone mode now gets worker crash/zombie recovery that was previously Django-only. Django behavior is unchanged.
- Lambda/serverless handlers marked **EXPERIMENTAL** (docstring warnings, one-time runtime log per warm container, and doc callouts). Handler logic unchanged.
- Coverage gate `fail_under` raised 13 → 20.

### Fixed

- Corrected stale `__version__` in `sqlery/__init__.py` (was `0.13.0`, now tracks the real release version).

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
