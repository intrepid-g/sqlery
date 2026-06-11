---
phase: 14-scheduled-job-staging
plan: "03"
subsystem: django-backend
tags:
  - staging
  - dual-table-api
  - test-coverage
  - sc1
  - sc2
  - sc3
dependency_graph:
  requires:
    - 14-01-SUMMARY.md
    - 14-02-SUMMARY.md
  provides:
    - dual-table DjangoBackend API (get_job_by_id, cancel_job, get_staged_jobs)
    - SC-1/SC-2/SC-3 test suite
  affects:
    - src/sqlery/django_sqlery/backend.py
    - tests/unit/test_django_backend.py
    - tests/unit/test_staging.py
tech_stack:
  added: []
  patterns:
    - dual-table fallback lookup (QueuedJob -> ScheduledJob)
    - mock-cursor pattern for advisory lock tests (mirrors test_partitioning.py)
key_files:
  created:
    - tests/unit/test_staging.py
  modified:
    - src/sqlery/django_sqlery/backend.py
    - tests/unit/test_django_backend.py
decisions:
  - "get_jobs() returns QueuedJob rows only; staged jobs visible via get_staged_jobs() — avoids heterogeneous rowset merge"
  - "test_get_jobs_returns_queued_jobs_only asserts on instance type (not ID set) to handle SQLite auto-increment collision"
metrics:
  duration: "~20 minutes"
  completed: "2026-06-11"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 14 Plan 03: Dual-table API Surface + SC-1/2/3 Test Suite Summary

DjangoBackend methods patched to span both `sqlery_queued_job` and `sqlery_scheduled_job`, plus a complete test suite proving all three Phase 14 success criteria.

## What Was Built

### Task 1: Dual-table API surface — `get_job_by_id`, `cancel_job`, `get_staged_jobs`

**`get_job_by_id`** now falls back to `ScheduledJob.objects.get` when `QueuedJob.DoesNotExist` is raised. The old single-table body is commented out per project convention.

**`cancel_job`** now tries `QueuedJob.objects.filter(id=job_id, status='queued').update(...)` first; if no rows updated, falls through to `ScheduledJob.objects.filter(id=job_id).delete()`. Both cases return `bool`.

**`get_staged_jobs(queue_name, limit, offset)`** is a new method returning `ScheduledJob` rows ordered by `scheduled_at`. This is the correct list API for staged rows — `get_jobs()` intentionally returns only the executable `QueuedJob` table to avoid a heterogeneous rowset merge.

**`get_jobs()`** received a one-line comment and docstring annotation directing callers to `get_staged_jobs()` for the staging table. No query logic changed.

### Task 2: `tests/unit/test_staging.py` — SC-1, SC-2, SC-3

**`TestStagingRouting` (5 tests, `@pytest.mark.django_db`):**
- `test_far_future_job_goes_to_staging` — create_job with +60 days returns a `ScheduledJob` instance
- `test_far_future_job_invisible_to_claim_queue` — `QueuedJob.objects.filter(id=job.id).exists()` is False
- `test_far_future_job_visible_to_get_job_by_id` — `get_job_by_id` returns the staged row
- `test_far_future_job_cancellable` — `cancel_job` returns True and deletes the row
- `test_near_future_job_goes_to_queued_job` — +12 hour job lands in `QueuedJob`

**`TestPromotion` (4 tests, mock cursor, no Django DB):**
- `test_skips_when_lock_not_acquired` — `pg_try_advisory_lock` returns False → returns 0, no DELETE
- `test_promotes_rows_when_lock_acquired` — 2 rows from DELETE RETURNING → returns 2, advisory_unlock called
- `test_returns_zero_when_no_due_rows` — lock acquired, empty result set → returns 0
- `test_advisory_unlock_called_even_on_insert_error` — INSERT raises → advisory_unlock still called in finally

**`TestStagingConfigValidation` (4 tests, no DB):**
- Valid config (threshold=1, retention=30 days) does not raise
- retention == threshold raises ValueError
- retention < threshold raises ValueError
- equal single-unit case (threshold=1, retention='1 day') raises ValueError

## Verification Results

```
env -u VIRTUAL_ENV uv run --extra dev --extra postgres pytest tests/unit/test_staging.py -v
-> 13 passed

env -u VIRTUAL_ENV uv run --extra dev --extra postgres pytest tests/unit/ -x -q
-> 498 passed, 11 skipped, 3 xfailed
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test assertion for `test_get_jobs_returns_queued_jobs_only` — SQLite ID collision**

- **Found during:** Task 1 GREEN phase
- **Issue:** In SQLite, `sqlery_queued_job` and `sqlery_scheduled_job` each have independent `sqlite_sequence` counters both starting at 1. When one row is inserted into each table, both get `id=1`. The original test checked `staged.id not in result_ids`, which failed because `staged.id == queued.id == 1` in SQLite.
- **Fix:** Changed assertion to verify every returned object is a `QueuedJob` instance (type check), which correctly proves the query excludes ScheduledJob rows regardless of ID value.
- **Files modified:** `tests/unit/test_django_backend.py` (test body only, no production code change)
- **Commit:** b4ac1db

## Known Stubs

None.

## Self-Check

- [x] `src/sqlery/django_sqlery/backend.py` modified — verified changes present
- [x] `tests/unit/test_django_backend.py` modified — `TestDualTableApiSurface` class with 10 tests
- [x] `tests/unit/test_staging.py` created — 13 tests covering SC-1/SC-2/SC-3
- [x] Commit b4ac1db exists (Task 1)
- [x] Commit 52ec032 exists (Task 2)
- [x] Full unit suite: 498 passed, 11 skipped, 3 xfailed — no regressions

## Self-Check: PASSED
