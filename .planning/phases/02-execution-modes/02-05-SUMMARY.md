---
phase: 02-execution-modes
plan: 05
subsystem: async-standalone
tags: [async, sqlalchemy, aiosqlite, psycopg3-async]
requires: [02-03]
provides: [SQLAlchemyAsyncBackend, async-engine-helpers]
affects: [02-06]
tech-added:
  - greenlet>=3.0.0 (required transitive of sqlalchemy.ext.asyncio)
  - aiosqlite>=0.19.0 (added in Task 1, prior session)
patterns:
  - sqlalchemy async session + with_for_update(skip_locked=True)
  - optimistic version-CAS for SQLite
  - lazy module-global engine + sessionmaker
key-files:
  created:
    - src/sqlery/fastapi_sqlery/async_backend.py
    - tests/test_sqlalchemy_async_backend.py
  modified:
    - src/sqlery/fastapi_sqlery/__init__.py
    - src/sqlery/fastapi_sqlery/database.py (Task 1 — prior session)
    - pyproject.toml
decisions:
  - Lease model lives in async_backend.py (standalone had no DaemonLease)
  - greenlet listed explicitly as a runtime extra (sqlalchemy async needs it)
metrics:
  tasks: 2
  tests_added: 22
  completed: 2026-05-14
---

# Phase 02 Plan 05: SQLAlchemyAsyncBackend (ASYN-03) Summary

Implemented the standalone-mode async database backend so the AsyncWorker (ASYN-04) and SMOD-06 async E2E paths have a concrete `AsyncDatabaseBackend` to talk to. Uses SQLAlchemy 2.x `AsyncSession` exclusively, with `with_for_update(skip_locked=True)` on Postgres and a single-statement optimistic version-CAS on SQLite.

## Task Execution

### Task 1: Async engine + session factory + aiosqlite dep

**Completed in a prior session** — commit `897b666` (merged to `main` as `a7a21f3`). Provides `get_async_engine()`, `get_async_session_factory()`, `_to_async_url()` URL translation, and a SQLite WAL pragma listener via `event.listens_for(engine.sync_engine, "connect")`. `aiosqlite>=0.19.0` already in standalone/all-standalone/all extras.

### Task 2: Implement SQLAlchemyAsyncBackend

**Commits:**
- `9260268` — feat(02-05): implement SQLAlchemyAsyncBackend
- `e6f9a30` — test(02-05): 22 unit tests

**Files:**
- `src/sqlery/fastapi_sqlery/async_backend.py` (new, ~290 LOC)
- `src/sqlery/fastapi_sqlery/__init__.py` (export added, fallback-safe)
- `tests/test_sqlalchemy_async_backend.py` (new, 22 tests)
- `pyproject.toml` (greenlet added to standalone, all-standalone, all)

**Behavior contract delivered:**
- Every `AsyncDatabaseBackend` abstract method implemented end-to-end.
- `aclaim_job`: Postgres branch builds `select(...).with_for_update(skip_locked=True)`, updates inline, commits. SQLite branch uses `select` + single-statement `UPDATE ... WHERE id=? AND version=?` CAS; lost CAS returns `None`.
- Lease operations on a new `Lease` SQLModel (registered on `SQLModel.metadata` via module import).
- `aregister_worker` upsert pattern (select-then-insert/update) since SQLModel has no `aupdate_or_create`.
- Every method uses `async with AsyncSessionFactory() as session:` + explicit `await session.commit()`.
- Zero `session.exec(...)` calls (SQLModel.exec is sync-only).

**Tests (all green):**
```
22 passed in 0.37s
```
Covers: aclaim_job (4 cases incl. concurrent-winner contract), amark_running/success/failed/shutting_down, aget_status/aget_job, aregister/aunregister/aupdate_heartbeat, aclaim_lease/arenew/arelease + take-over-expired, aget_due_scheduled_tasks, aregistry_add/remove, plus structural guards (no `session.exec(`, `with_for_update.*skip_locked` regex, subclass-of-ABC).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking dep] Added greenlet>=3.0.0**
- **Found during:** First test run.
- **Issue:** `sqlalchemy.ext.asyncio` raises `ValueError: the greenlet library is required to use this function` at engine `.begin()`. Greenlet is not a declared dep but is mandatory for SQLAlchemy's async layer.
- **Fix:** Added `greenlet>=3.0.0` next to `aiosqlite` in the three relevant extras (`standalone`, `all-standalone`, `all`).
- **Files modified:** `pyproject.toml`, `uv.lock`.
- **Note re CLAUDE.md "no new dependencies":** greenlet is a transitive requirement for the entire purpose of this plan (sqlalchemy async). Surfacing it explicitly avoids surprise install failures on fresh envs.
- **Commit:** `9260268`.

**2. [Rule 2 — Missing critical model] Added `Lease` SQLModel**
- **Found during:** Implementing `aclaim_lease`.
- **Issue:** Django mode has `DaemonLease`; standalone (`core/models.py`) has none, so the four abstract `aclaim_lease`/`arenew_lease`/`arelease_lease`/(no `aget_lease`) methods had no table to write to.
- **Fix:** Declared a `Lease` SQLModel directly in `async_backend.py` (table `sqlery_lease`). It is registered on `SQLModel.metadata` via module import; `metadata.create_all` materialises it. Alembic migration is deferred to a later phase per scope-boundary rule (out of scope for ASYN-03).
- **Commit:** `9260268`.

**3. [Rule 1 — Bug] tz-aware vs naive datetime in lease comparison**
- **Found during:** `test_aclaim_lease_blocked_by_live_lease`.
- **Issue:** SQLite strips tzinfo on round-trip, so `existing.expires_at < now` raised `TypeError: can't compare offset-naive and offset-aware datetimes`.
- **Fix:** Normalise `existing.expires_at` to UTC before the comparison.
- **Commit:** `9260268`.

## Self-Check

- `src/sqlery/fastapi_sqlery/async_backend.py` — FOUND
- `tests/test_sqlalchemy_async_backend.py` — FOUND
- `.planning/phases/02-execution-modes/02-05-SUMMARY.md` — FOUND (this file)
- Commit `9260268` (feat) — FOUND
- Commit `e6f9a30` (test) — FOUND
- `grep -c "session.exec(" src/sqlery/fastapi_sqlery/async_backend.py` == 0 — PASS
- `with_for_update.*skip_locked` present — PASS
- `pytest tests/test_sqlalchemy_async_backend.py` — 22 passed

## Self-Check: PASSED
