---
phase: 16-backend-wiring-pruning
plan: "03"
subsystem: backend
tags:
  - partitioning
  - cleanup
  - vacuum
  - write-path-pruning
  - daemon-metrics
dependency_graph:
  requires:
    - 16-01
  provides:
    - cleanup_jobs routes to reclaim_drained_partitions on PG (D5)
    - vacuum_database skips partitioned table on PG (D5/R3)
    - write-path items 7-11 use created_at in UPDATE filters
    - async_backend mirrors cleanup routing and terminal-status write filters
    - all 5 operator metrics in daemon._last_partition_stats (R9 complete)
  affects:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/async_backend.py
    - src/sqlery/core/daemon.py
tech_stack:
  added: []
  patterns:
    - two-step SELECT created_at then UPDATE with partition pruning filter
    - D5 destroy-by-default partition reclaim via reclaim_drained_partitions
    - R9 operator metrics dict on DaemonManager._last_partition_stats
key_files:
  created: []
  modified:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/async_backend.py
    - src/sqlery/core/daemon.py
decisions:
  - cancel_job and mark_job_archived use two-step SELECT+UPDATE (no full job object available at call site)
  - cascade_ancestor_status fetches created_at + parent_job_id in single query per loop iteration
  - update_job_child_pid gains optional created_at param — callers without it degrade gracefully
  - get_job_by_id verified as SELECT only — no change needed (item 10 checklist)
  - acleanup_jobs in async_backend is a stub that warns on PG+partitioned and defers to sync backend
  - oldest_undrained_age_days uses max() over past-upper-bound partitions (partitions inside window excluded)
  - staging_depth guarded with ScheduledJob is not None for standalone-mode compatibility
metrics:
  duration: "~30 min"
  completed: "2026-06-12"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 16 Plan 03: Cleanup Routing, Vacuum Skip, Write-Path Items 7-11, Daemon Metrics Summary

Django backend wired to partition machinery end-to-end: cleanup_jobs routes to reclaim_drained_partitions on PG (D5 with loud destroy-by-default comment), vacuum_database skips VACUUM on partitioned table, write-path UPDATE methods gain created_at partition-pruning filters, async_backend mirrors changes, and daemon maintenance tick now emits all 5 operator metrics (R9 complete).

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | cleanup_jobs routing + vacuum skip + write-path items 7-11 + async mirror | a44a10d | backend.py, async_backend.py |
| 2 | 4 remaining operator metrics in daemon maintenance tick (R9) | 98e15d0 | daemon.py |

## Changes Made

### Task 1 — backend.py + async_backend.py

**cleanup_jobs (D5/D6):**
- When `self._partitioned_pg()` is True and `_partitioning` is available: routes to `_partitioning.reclaim_drained_partitions(cur, table, retention_str, archive_hook)` with a loud D5 comment explaining that partition DROP destroys all jobs beyond retention unless `SQLERY_PARTITION_ARCHIVE_HOOK` is configured. Returns `{"deleted": 0, "reclaimed_via_partition_drop": True, "dropped_partitions": N, "note": "..."}`.
- When False: Phase-12 keyset-batched DELETE loop preserved byte-for-byte (D6 unchanged).
- Added top-level import: `from sqlery.core import partitioning as _partitioning` inside try/except ImportError guard.

**vacuum_database:**
- Old unconditional `cursor.execute("VACUUM ANALYZE sqlery_queued_job")` commented out.
- Replaced with `if not self._partitioned_pg(): cursor.execute("VACUUM ANALYZE sqlery_queued_job")` plus comment explaining why (partition DROP leaves nothing to vacuum).
- Other tables (sqlery_scheduled_task, sqlery_registry, sqlery_worker) remain unchanged.

**Write-path items 7-11:**
- Item 7 (cancel_job): Two-step pattern — SELECT created_at first, then UPDATE with id+created_at+status filter.
- Item 8 (mark_job_archived): Same two-step pattern for status="failed" filter.
- Item 9 (cascade_ancestor_status): Loop now fetches `created_at` and `parent_job_id` together in one `.values()` query per iteration, then UPDATEs with created_at in filter.
- Item 10 (get_job_by_id): Verified as SELECT only — full row returned, no `.only()` dropping created_at. No change needed; comment added.
- Item 11 (update_job_child_pid): Signature gains `created_at=None` optional param. When provided, `filter_kwargs["created_at"] = created_at` for partition pruning. Existing callers degrade gracefully.

**async_backend.py:**
- Added `acleanup_jobs(**kwargs)` method: warns on PG+partitioned path and returns `{"skipped": True, "reason": "partition_reclaim_sync_only"}`; on SQLite returns `{"skipped": True, "reason": "use_sync_cleanup_jobs"}`.
- `amark_running`, `amark_success`, `amark_failed`, `amark_shutting_down`: each now does a prior `afirst()` to get created_at, then filters with id+created_at in the aupdate(). Old id-only filter lines commented out.

### Task 2 — daemon.py

**Imports:**
- Added `ScheduledJob` to the guarded `from ..django_sqlery.models import QueuedJob, ScheduledJob` import block.

**DaemonManager.__init__:**
- Added `self._last_partition_stats: dict = {}` for operator access to metrics.

**Maintenance tick block:**
- Added `tick_start = time.monotonic()` before the `cur = backend.get_raw_cursor()` call.
- After `default_count`: calls `_partitioning._list_partitions(cur, partition_table)` to get all partition rows.
- Computes `partition_count = len([p for p in partitions if p[1] is not None])` (excludes DEFAULT with upper_bound=None).
- Computes `oldest_undrained_age_days`: max days since upper_bound for past-upper-bound partitions; None if none exist.
- Computes `staging_depth = ScheduledJob.objects.count() if ScheduledJob is not None else None`.
- Computes `tick_duration = time.monotonic() - tick_start` after all metrics.
- Updated `logger.info` to emit all 5 metrics in one message.
- Sets `self._last_partition_stats = {"partition_count": ..., "default_rows": ..., "oldest_undrained_age_days": ..., "staging_depth": ..., "last_tick_duration_s": ..., "last_tick_at": ...}`.

## Deviations from Plan

### Auto-fixed Issues

None.

### Notes

1. `tests/test_batched_cleanup.py` was already failing before this plan (psycopg module not installed in local venv — pre-existing environment issue, not introduced by this plan). Verified by checking baseline with `git stash`.
2. The `acleanup_jobs` async method is a warning-and-skip stub rather than a full implementation because `reclaim_drained_partitions` requires a synchronous psycopg cursor not available in async context.
3. For `oldest_undrained_age_days`: the metric reports the age since the partition's upper_bound (not the cutoff). Partitions still inside the retention window are excluded.

## Verification

```
grep -c "_partitioned_pg" src/sqlery/django_sqlery/backend.py  # → 13 (≥ 3 required)
grep -n "D5:" src/sqlery/django_sqlery/backend.py  # → lines 601, 619
grep -n "partition_count\|staging_depth\|tick_duration\|oldest_undrained" src/sqlery/core/daemon.py  # hits in maintenance tick block
```

Unit test counts (no new failures vs baseline):
- Before: 36 failed, 372 passed, 8 skipped, 3 xfailed, 5 warnings, 95 errors
- After: 36 failed, 372 passed, 8 skipped, 3 xfailed, 5 warnings, 95 errors

## Known Stubs

`acleanup_jobs` in async_backend.py returns `{"skipped": True}` — intentional: partition reclaim is synchronous-only. The daemon invokes sync backend for cleanup; async cleanup is deferred to a future plan if needed.

## Threat Flags

None — no new network endpoints, auth paths, or file access patterns introduced.

## Self-Check: PASSED

- src/sqlery/django_sqlery/backend.py: modified (cleanup routing + vacuum skip + items 7-11)
- src/sqlery/django_sqlery/async_backend.py: modified (acleanup_jobs stub + terminal-status write filters)
- src/sqlery/core/daemon.py: modified (4 new metrics + _last_partition_stats)
- Commit a44a10d: exists (Task 1)
- Commit 98e15d0: exists (Task 2)
