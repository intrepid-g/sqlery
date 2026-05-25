---
phase: 260525-myr
plan: "01"
subsystem: compat/rq
tags: [compat, rq, standalone, backend-agnostic, migration]
dependency_graph:
  requires: []
  provides: [rq-standalone-compat]
  affects: [src/sqlery/compat/rq.py]
tech_stack:
  added: []
  patterns: [lazy-import, mode-dispatch, inline-dataclass]
key_files:
  created:
    - tests/test_compat_rq_standalone.py
  modified:
    - src/sqlery/compat/rq.py
decisions:
  - "Copy Retry and JobStatus inline into rq.py rather than importing from scheduler.py (which has top-level Django imports), keeping rq.py Django-free at module level"
  - "Use is_django_mode() + get_backend() imported at module level from sqlery.compat (safe — compat/__init__.py has no Django top-level imports)"
  - "Queue.enqueue* methods branch on is_django_mode(): Django path passes Callable, standalone path converts to dotted task_path string"
  - "requeue_if_jobs_pending standalone path calls count_jobs() then subtracts 1 for the current job, approximating the Django exclude(pk=...).count() pattern"
  - "Job.delete() in standalone mode calls cancel_job() — DatabaseBackend ABC has no delete_job method; documented in docstring"
metrics:
  duration: "~8 minutes"
  completed: "2026-05-25"
  tasks_completed: 3
  files_changed: 2
---

# Phase 260525-myr Plan 01: Make compat/rq.py Backend-Agnostic Summary

**One-liner:** Backend-agnostic RQ compat layer via lazy Django imports, inline Retry/JobStatus, and mode-dispatched utility functions using the DatabaseBackend ABC.

## What Was Built

`src/sqlery/compat/rq.py` previously hard-imported four Django modules at the top level, making it fail on import in any non-Django process. The module now:

1. Imports `is_django_mode` and `get_backend` from `sqlery.compat` (no Django deps at module level).
2. Defines `Retry` and `JobStatus` inline (copied from scheduler.py, which has top-level Django deps).
3. Provides a `_make_queue(name)` factory that returns `DjangoQueue` in Django mode and `core.job_queue.Queue` in standalone mode.
4. Routes all six utility functions (`get_job_registry_summary`, `clear_failed_jobs`, `delete_other_jobs_by_same_meta_tag`, `get_queue_wait_time`, `requeue_if_jobs_pending`, `is_final_retry`) through `get_backend()` in standalone mode, preserving the Django fast-path identically.
5. Routes `Job.fetch()`, `Job.delete()`, and `Worker.all()` through the backend ABC in standalone mode.
6. `__all__` is byte-for-byte identical to the original.

A new test file `tests/test_compat_rq_standalone.py` (9 tests, no Django test infrastructure) covers the import smoke test and all standalone code paths via a `MockBackend` stub.

## Commits

| Commit | Description |
|--------|-------------|
| a1ea763 | feat(260525-myr-01): rewrite rq.py with lazy Django imports and _make_queue() factory |
| f91e37e | feat(260525-myr-01): add standalone utility function routing + test suite |

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

- [x] `src/sqlery/compat/rq.py` exists and `grep -c "^from sqlery.django_sqlery\|^from django"` returns 0
- [x] `tests/test_compat_rq_standalone.py` exists with 9 tests
- [x] All 9 tests pass
- [x] `__all__` unchanged (14 names, identical order)
- [x] Existing scheduler_compat tests (48) pass unaffected

## Self-Check: PASSED
