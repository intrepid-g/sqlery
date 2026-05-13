---
phase: 02-execution-modes
plan: 03
subsystem: compat
tags: [async, abc, compat]
requires: []
provides: [AsyncDatabaseBackend]
affects: [src/sqlery/compat/__init__.py]
tech_added: []
patterns: [Strategy pattern (async sibling to sync DatabaseBackend ABC)]
key_files_created:
  - tests/test_async_backend_abc.py
key_files_modified:
  - src/sqlery/compat/__init__.py
decisions:
  - "Hot-path-only ABC (16 methods), not full 30+ mirror — daemon/scheduler stay sync."
  - "Included aget_status + aget_job per plan-checker fix for 02-08 async E2E harness."
metrics:
  duration: <5 minutes
  tasks_completed: 1
  files_changed: 2
  completed_at: 2026-05-13
requirements: [ASYN-01]
---

# Phase 02 Plan 03: AsyncDatabaseBackend ABC Summary

Defined the `AsyncDatabaseBackend` abstract base class in `src/sqlery/compat/__init__.py`, pinning the async contract for ASYN-01 so ASYN-02 (DjangoAsyncBackend) and ASYN-03 (SQLAlchemyAsyncBackend) can be built in parallel.

## What Was Done

- Added 16 `@abstractmethod async def` declarations covering claim, lifecycle, heartbeat, registration, lease, scheduling, and registry hot paths.
- Each method has a one-line docstring naming its sync analog.
- Exported `AsyncDatabaseBackend` via `__all__`.
- TDD: RED test committed first (import failed), then GREEN implementation.

## Tasks

| Task | Name                          | Commit  | Files                                                              |
| ---- | ----------------------------- | ------- | ------------------------------------------------------------------ |
| 1 (RED)  | Failing tests for ABC     | b3ba41d | tests/test_async_backend_abc.py                                    |
| 1 (GREEN)| Add AsyncDatabaseBackend  | cf117ae | src/sqlery/compat/__init__.py                                      |

## Verification

- `pytest tests/test_async_backend_abc.py` → 5 passed.
- Name-based abstract-method check from plan verification block → OK.
- Sync `DatabaseBackend` ABC unchanged (no behavioral regression).

## Deviations from Plan

None — plan executed exactly as written.

## TDD Gate Compliance

- RED commit: `b3ba41d test(02-03): ...`
- GREEN commit: `cf117ae feat(02-03): ...`
- REFACTOR: not needed (single-pass clean implementation).

## Known Stubs

None. The ABC is intentionally abstract; concrete backends arrive in ASYN-02 / ASYN-03.

## Self-Check: PASSED

- FOUND: src/sqlery/compat/__init__.py (contains `class AsyncDatabaseBackend`)
- FOUND: tests/test_async_backend_abc.py
- FOUND: commit b3ba41d (RED)
- FOUND: commit cf117ae (GREEN)
