---
phase: "17-fastapi-parity"
plan: "03"
subsystem: "standalone-backend"
tags:
  - partition-routing
  - write-path-pruning
  - staging-dual-table
  - sqlalchemy

dependency_graph:
  requires:
    - "17-01"  # composite PK on QueuedJob + ScheduledJob models
    - "17-02"  # partitioned DDL in database.py
  provides:
    - "_partitioned_pg() on SQLAlchemyBackend + SQLAlchemyAsyncBackend"
    - "cleanup_jobs → reclaim_drained_partitions routing on partitioned PG"
    - "staging dual-table surface (create_job, get_job_by_id, cancel_job, get_staged_jobs)"
    - "write-path created_at pruning (mark_job_archived, cascade_ancestor_status, update_job_child_pid, release_claimed_job)"
    - "async amark_* write-path pruning"
  affects:
    - "src/sqlery/fastapi_sqlery/backend.py"
    - "src/sqlery/fastapi_sqlery/async_backend.py"

tech_stack:
  added: []
  patterns:
    - "WR-01: transient-error-safe cache (leave None on exception, only write on success)"
    - "D5: cleanup → reclaim_drained_partitions routing with try/finally cursor close (CR-02)"
    - "D6: SQLite / non-partitioned PG keeps batched DELETE path unchanged"
    - "R5: far-future create_job → ScheduledJob gated on _partitioned_pg()"
    - "WR-03: cascade_ancestor_status excludes terminal-status ancestors"

key_files:
  created:
    - "tests/unit/test_sqlalchemy_backend_partitions.py"
  modified:
    - "src/sqlery/fastapi_sqlery/backend.py"
    - "src/sqlery/fastapi_sqlery/async_backend.py"

decisions:
  - "D6 gate on all partition-only paths — SQLite never routes to partition logic"
  - "WR-01 pattern: _partitioned_pg() cache left None on transient error, not set to False"
  - "CR-02 fix: cleanup_jobs wraps raw cursor in try/finally to close cursor + raw connection"
  - "SQLModel exec() returns scalar for single-column selects — use direct value not tuple subscript"
  - "Async _partitioned_pg() delegates to sync engine catalog query (acceptable for cache-on-first-call)"

metrics:
  duration_seconds: 2092
  completed_date: "2026-06-12"
  tasks_completed: 2
  files_modified: 3
---

# Phase 17 Plan 03: Partition Wiring + Write-Path Pruning in SQLAlchemy Backends Summary

Wired partition machinery into `SQLAlchemyBackend` (sync) and `SQLAlchemyAsyncBackend` (async), mirroring the Django Phase-16 backend. All SQLite paths remain unchanged (D6). Tests confirm 27 new cases pass with 528 total passing.

## What Was Built

**`_partitioned_pg()` method (sync + async):** Queries the `pg_class` catalog for `sqlery_queued_job` partition status on PostgreSQL. Returns False on SQLite (dialect check, no DB call), caches the result per-process. WR-01 pattern: on transient DB error the cache is NOT written (stays None) so the next call retries — avoids permanently disabling partition routing on a startup connection failure.

**`get_raw_cursor()` method (sync backend):** Returns a raw psycopg DBAPI cursor via `engine.raw_connection().cursor()` on partitioned PG; returns None on SQLite. Caller owns the cursor lifecycle (must close cursor + connection). Docstring documents this to prevent CR-02 cursor leaks.

**`cleanup_jobs` partition routing (D5):** When `_partitioned_pg()` is True, routes to `reclaim_drained_partitions(cur, ...)` from `core.partitioning`. Gets the raw cursor inside a `try/finally` block that closes both the cursor and the underlying raw connection (fixes the CR-02 leak pattern). Returns `{"reclaimed_via_partition_drop": True, ...}` with loud D5 comment. On SQLite/non-partitioned PG, the Phase-12 batched DELETE loop is byte-for-byte unchanged (D6).

**`vacuum_database` skip (D5/R3):** When `_partitioned_pg()` is True, skips `VACUUM ANALYZE sqlery_queued_job` — partition DROP leaves nothing to vacuum on the parent; individual partitions are autovacuumed per-child. Mirrors DjangoBackend Phase-16 carry-forward.

**Staging dual-table surface (R5):** `create_job` routes far-future jobs (> now + threshold days) to `ScheduledJob` only when `_partitioned_pg()`. Full payload stored as `{"kwargs": ..., "job_spec": {...}}` for lossless promotion. `get_job_by_id` falls back to `ScheduledJob` when not found in `QueuedJob` on partitioned PG. `cancel_job` checks `ScheduledJob` and DELETEs the row when the job isn't found as a queued `QueuedJob`. `get_staged_jobs()` returns `[]` on SQLite, `ScheduledJob` rows ordered by `scheduled_at` on partitioned PG.

**Write-path pruning (items 7–11):** `mark_job_archived` fetches `created_at` first via single-column select, then UPDATEs with `(id, created_at, status)` filter. `cascade_ancestor_status` fetches `(created_at, parent_job_id)` per iteration and UPDATEs with `(id, created_at)` filter; WR-03 excludes terminal-status ancestors. `update_job_child_pid` gains optional `created_at` parameter. `release_claimed_job` uses `job.created_at` when available. `mark_job_success`/`mark_job_failed` use `get_job_by_id` for staging span and add `hasattr` guard against calling `mark_success`/`mark_failed` on `ScheduledJob` (IN-01).

**Async write-path pruning:** `amark_running`, `amark_success`, `amark_failed`, `amark_shutting_down` fetch `created_at` via SELECT before the UPDATE when `_partitioned_pg()` is True; fall back to id-only filter on SQLite. The extra SELECT round-trip is acceptable as these are not on the hot claim path.

## Commits

- `64a0c12`: `test(17-03): add failing tests for partition-aware backend methods` (RED)
- `2608c7b`: `feat(17-03): wire partition machinery into SQLAlchemy backends` (GREEN)

## TDD Gate Compliance

- RED gate commit: `64a0c12` (19 failing tests)
- GREEN gate commit: `2608c7b` (27 passing tests)
- No separate REFACTOR commit needed (code was clean as written)

## Test Results

- New tests: 27 passing (all partition-aware path coverage)
- Existing SQLite standalone tests: 89 passing, no regressions
- Full test suite: 528 passed, 28 skipped, 3 xfailed — no new failures

## Deviations from Plan

**[Rule 1 - Bug] SQLModel single-column exec() returns scalar, not tuple**
- Found during: Task 2 (GREEN phase)
- Issue: `session.exec(select(QueuedJob.created_at).where(...)).first()` returns the datetime scalar directly, not a `(datetime,)` tuple. `row[0]` raises `TypeError: 'datetime' object is not subscriptable`.
- Fix: Used the scalar value directly (`created_at_val = session.exec(...).first()`) instead of subscripting it.
- Files modified: `src/sqlery/fastapi_sqlery/backend.py`
- Commit: `2608c7b`

**[Rule 1 - Bug] Test used `from sqlalchemy import select` instead of sqlmodel's**
- Found during: Task 2 test fix
- Issue: Test helper used SQLAlchemy's `select` in `session.exec()` which returns a Row namedtuple, not an ORM model instance; `db_job.status = "failed"` raised `AttributeError: can't set attribute`.
- Fix: Changed test to use `from sqlmodel import select as sqlmodel_select`.
- Files modified: `tests/unit/test_sqlalchemy_backend_partitions.py`
- Commit: `2608c7b`

None — plan executed as specified for all other items.

## Known Stubs

None. All methods are fully wired; SQLite falls back to D6 paths as designed.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: raw-cursor-lifecycle | `src/sqlery/fastapi_sqlery/backend.py` | `get_raw_cursor()` returns a DBAPI cursor; caller must close cursor + raw connection. CR-02 pattern mitigated via try/finally in `cleanup_jobs`. |

## Self-Check: PASSED

- `src/sqlery/fastapi_sqlery/backend.py` modified: CONFIRMED (git show 2608c7b)
- `src/sqlery/fastapi_sqlery/async_backend.py` modified: CONFIRMED (git show 2608c7b)
- `tests/unit/test_sqlalchemy_backend_partitions.py` created: CONFIRMED (git show 64a0c12)
- Commits `64a0c12` and `2608c7b` exist: CONFIRMED (git log)
- 528 tests passing: CONFIRMED (pytest run above)
