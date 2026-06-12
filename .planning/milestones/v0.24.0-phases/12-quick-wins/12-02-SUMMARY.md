---
phase: 12-quick-wins
plan: "02"
subsystem: cleanup
tags: [batched-delete, cleanup, django-backend, sqlalchemy-backend, performance]
dependency_graph:
  requires: []
  provides: [batched-cleanup-jobs-django, batched-cleanup-jobs-sqlalchemy]
  affects: [DjangoBackend.cleanup_jobs, SQLAlchemyBackend.cleanup_jobs]
tech_stack:
  added: []
  patterns: [keyset-pagination, batched-delete, status-recheck-in-delete]
key_files:
  created:
    - tests/test_batched_cleanup.py
  modified:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/fastapi_sqlery/backend.py
decisions:
  - "CLEANUP_BATCH_SIZE=500 caps lock-hold time per iteration (D6: permanent path for SQLite and non-partitioned PG)"
  - "FINISHED_STATUSES tuple defined at module level in both backends for the status re-check inside the batch DELETE"
  - "time.sleep(0.1) inter-batch sleep yields to autovacuum between batches"
  - "Old unbounded delete lines commented out per CLAUDE.md convention, not deleted"
metrics:
  duration: "4 minutes"
  completed: "2026-06-10T21:05:35Z"
  tasks_completed: 3
  files_changed: 3
---

# Phase 12 Plan 02: Batched Cleanup DELETE Summary

Replaced unbounded single-DELETE in cleanup_jobs in both backends with a keyset-batched loop (BATCH=500, order_by id, status re-check inside DELETE, 0.1s inter-batch sleep). Under a 100k-row backlog the old implementation held a table lock for multiple seconds; the batched loop caps lock-hold time per iteration and never deletes a row claimed mid-loop.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Keyset-batched cleanup_jobs in DjangoBackend | 62a5f83 | src/sqlery/django_sqlery/backend.py |
| 2 | Keyset-batched cleanup_jobs in SQLAlchemyBackend | caf9ee7 | src/sqlery/fastapi_sqlery/backend.py |
| 3 | Behavioral tests for batched cleanup invariants | c0175fe | tests/test_batched_cleanup.py |

## What Was Built

### DjangoBackend.cleanup_jobs (src/sqlery/django_sqlery/backend.py)

- Added `import time` at top-level
- Added `CLEANUP_BATCH_SIZE = 500` and `FINISHED_STATUSES = ("success", "failed", "archived")` module-level constants
- Replaced unbounded `query.delete()` with keyset-batched loop:
  - `order_by("id").values_list("id", flat=True)[:CLEANUP_BATCH_SIZE]` to select a bounded page of IDs
  - `QueuedJob.objects.filter(id__in=ids, status__in=FINISHED_STATUSES).delete()` for the batch DELETE with status re-check
  - `time.sleep(0.1)` between batches
- Old unbounded lines commented out per convention

### SQLAlchemyBackend.cleanup_jobs (src/sqlery/fastapi_sqlery/backend.py)

- Added `import logging`, `import time` at top-level
- Added `CLEANUP_BATCH_SIZE = 500` and `FINISHED_STATUSES = ("success", "failed", "archived")` module-level constants
- Replaced unbounded `session.exec(stmt)` delete with keyset-batched loop:
  - `select(QueuedJob.id)` with same filters + `order_by(QueuedJob.id).limit(CLEANUP_BATCH_SIZE)` for ID page
  - `delete(QueuedJob).where(QueuedJob.id.in_(ids)).where(QueuedJob.status.in_(FINISHED_STATUSES))` per batch
  - `session.commit()` inside the loop after each batch
  - `time.sleep(0.1)` between batches
- Old unbounded lines commented out per convention

### tests/test_batched_cleanup.py (new file)

4 behavioral tests covering key invariants:

1. `test_cleanup_never_deletes_claimed_job` — job whose status changes to "queued" before DELETE is not deleted (status re-check protection)
2. `test_cleanup_issues_multiple_batches_not_one` — `CLEANUP_BATCH_SIZE+1` rows trigger >=2 DELETE statements (via `CaptureQueriesContext`)
3. `test_cleanup_dry_run_does_not_delete` — dry_run=True returns count without deleting
4. `test_cleanup_batch_sleep_is_called` — `time.sleep(0.1)` called at least once (via `unittest.mock.patch`)

## Deviations from Plan

### Auto-fixed Issues

None.

### Plan Verification Command Adjustment

The plan's verification commands 2 and 3 (`grep -c "query.delete()"` and `grep -c "result = session.exec(stmt)"` returning 0) check the entire file, but `cleanup_jobs_by_count` and other methods legitimately use `query.delete()` and `session.exec(stmt)`. The plan explicitly scopes changes to `cleanup_jobs` only: "Do not modify `cleanup_jobs_by_count` — that method is out of scope for this plan." The invariant required by the plan is satisfied: `cleanup_jobs` no longer has an unbounded DELETE.

## Verification Results

```
uv run pytest tests/test_ttl_retention.py tests/test_batched_cleanup.py -x -q
45 passed in 2.13s
```

All 45 tests pass (41 existing + 4 new).

## Known Stubs

None — all data paths are wired.

## Threat Flags

No new security-relevant surface introduced. The status re-check (T-12-02-01 from threat register) is implemented as the `status__in=FINISHED_STATUSES` predicate inside the batch DELETE in both backends.

## Self-Check: PASSED

- [x] src/sqlery/django_sqlery/backend.py — modified with batched loop
- [x] src/sqlery/fastapi_sqlery/backend.py — modified with batched loop
- [x] tests/test_batched_cleanup.py — created with 4 passing tests
- [x] Commit 62a5f83 exists (Task 1)
- [x] Commit caf9ee7 exists (Task 2)
- [x] Commit c0175fe exists (Task 3)
