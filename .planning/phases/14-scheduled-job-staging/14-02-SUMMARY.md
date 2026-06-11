---
phase: 14-scheduled-job-staging
plan: "02"
subsystem: scheduled-job-staging
tags:
  - staging
  - enqueue-routing
  - partition-maintenance
  - advisory-locks
  - tdd
dependency_graph:
  requires:
    - 14-01  # ScheduledJob model + migration 0029
  provides:
    - enqueue-routing-threshold     # create_job routes far-future jobs to sqlery_scheduled_job
    - promote-due-scheduled-jobs    # promotion function in scheduler.py
    - staging-config-validation     # _validate_staging_config in daemon.py
    - daemon-promotion-tick         # promotion hooked into partition maintenance cycle
  affects:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/settings.py
    - src/sqlery/core/scheduler.py
    - src/sqlery/core/daemon.py
tech_stack:
  added:
    - "psycopg guard in scheduler.py (try/except ImportError)"
    - "ADVISORY_LOCK_PROMOTE = int.from_bytes(b'SQLEPROM', 'big')"
  patterns:
    - "Advisory-lock try/finally pattern (mirrors partitioning.py)"
    - "FOR UPDATE SKIP LOCKED for exactly-once promotion"
    - "Comment-out-and-replace convention for changed import lines"
key_files:
  created: []
  modified:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/settings.py
    - src/sqlery/core/scheduler.py
    - src/sqlery/core/daemon.py
    - tests/unit/test_django_backend.py
    - tests/unit/test_daemon.py
decisions:
  - "Boundary is exclusive: scheduled_at strictly greater than threshold goes to staging (not >=)"
  - "promote_due_scheduled_jobs is module-level, not a class method — takes raw cursor, no Django/SQLAlchemy"
  - "_validate_staging_config computes retention in fractional days to handle hour/minute units"
  - "Promotion placed inside partition maintenance try block so it shares the same cursor and advisory-lock session"
  - "Config validation error is logged but does not disable partition_maintenance_enabled (staging advisory only)"
metrics:
  duration: "~12 minutes"
  completed: "2026-06-11"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 6
---

# Phase 14 Plan 02: Enqueue Routing + Promotion + Daemon Wiring Summary

Implemented the three moving parts that make staged job scheduling work end-to-end: enqueue routing in DjangoBackend.create_job, exactly-once promotion in core/scheduler.py, and daemon wiring with config validation.

## Tasks Completed

### Task 1: Enqueue routing in DjangoBackend.create_job (TDD)

**RED commit:** `af00e95` — 4 failing tests for ScheduledJob/QueuedJob routing  
**GREEN commit:** `f9e9bf9` — implementation

**What was built:**

- Added `SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS: 1` to `settings.py` DEFAULTS with D1 comment
- Imported `ScheduledJob` and `get_setting` in `backend.py` (comment-out-and-replace convention)
- Added `self.ScheduledJob = ScheduledJob` to `DjangoBackend.__init__`
- Added threshold routing block in `create_job` between the job_name conflict-cleanup and the QueuedJob.objects.create call:
  - Reads `SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS` via `get_setting`
  - Routes `scheduled_at > now_utc + timedelta(days=threshold)` to `ScheduledJob.objects.create`
  - All other cases (None, near-future, exact boundary) fall through to `QueuedJob.objects.create`

**Boundary decision:** strictly greater than (not `>=`) — boundary case goes to main queue so it can be claimed immediately as it enters its execution window.

### Task 2: promote_due_scheduled_jobs + daemon wiring + config validation (TDD)

**RED commit:** `e3de3e9` — 8 failing tests (4 mock-cursor + 4 config validation)  
**GREEN commit:** `515f4a3` — implementation

**scheduler.py additions:**
- `try/except ImportError` psycopg guard at module level (matches partitioning.py pattern)
- `ADVISORY_LOCK_PROMOTE: int = int.from_bytes(b"SQLEPROM", "big")` module constant
- `_PROMOTION_LOOKAHEAD_SECONDS: int = 30` lookahead window constant
- `promote_due_scheduled_jobs(cur) -> int` module-level function:
  - Acquires `pg_try_advisory_lock(ADVISORY_LOCK_PROMOTE)`; returns 0 if not acquired
  - `DELETE FROM sqlery_scheduled_job WHERE scheduled_at <= now() + make_interval(secs => 30) FOR UPDATE SKIP LOCKED RETURNING ...`
  - For each returned row: `INSERT INTO sqlery_queued_job (...) VALUES (..., 'queued', ...)`
  - `finally:` always calls `pg_advisory_unlock(ADVISORY_LOCK_PROMOTE)`
  - Returns `len(rows)` promoted

**daemon.py additions:**
- `_validate_staging_config(threshold_days, retention_str) -> None` module-level function
  - Parses retention string with regex (supports day/hour/minute units)
  - Converts to fractional days and raises ValueError if retention <= threshold
- Import updated: `from .scheduler import Scheduler, promote_due_scheduled_jobs`
- In `_run_daemon`: reads `staging_threshold_days = get_config("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS", 1)`
- In startup validation block: calls `_validate_staging_config` (logs error but does not disable maintenance)
- In partition maintenance tick: calls `promote_due_scheduled_jobs(cur)` after `check_default_partition`, inside the same try/except

## Test Results

```
tests/unit/ — 475 passed, 11 skipped, 3 xfailed (no regressions)
```

4 new enqueue-routing tests in `test_django_backend.py`:
- `test_far_future_creates_scheduled_job_not_queued_job`
- `test_near_future_creates_queued_job`
- `test_no_scheduled_at_creates_queued_job`
- `test_exact_threshold_boundary_creates_queued_job`

8 new tests in `test_daemon.py`:
- `TestPromoteDueScheduledJobs` × 4 (mock cursor, no live DB)
- `TestValidateStagingConfig` × 4 (ValueError for retention ≤ threshold, pass otherwise)

## Deviations from Plan

### Auto-adjustments

**1. [Rule 2 - Missing validation] _validate_staging_config accepts fractional days**
- **Found during:** Task 2 implementation
- **Issue:** Plan described `retention_days <= threshold_days` comparison, but `"48 hours"` is a legitimate retention value that should pass when threshold is 1 day (48 hours = 2 days > 1 day)
- **Fix:** Computed retention in fractional days using `{day: 1, hour: 1/24, minute: 1/1440}` multipliers so hour/minute units compare correctly
- **Files modified:** `src/sqlery/core/daemon.py`

**2. [Rule 2 - Missing validation] promote_due_scheduled_jobs logs warning when psycopg unavailable**
- **Found during:** Task 2 implementation
- **Issue:** Plan said "log a warning and return 0" when `_PSYCOPG_AVAILABLE` is False — implemented exactly per plan spec
- **Files modified:** `src/sqlery/core/scheduler.py`

None - plan executed as written otherwise.

## Verification

All plan verification commands pass:

```
env -u VIRTUAL_ENV uv run python -c "from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE; print('ok')"
# → ok

env -u VIRTUAL_ENV uv run python -c "from sqlery.core.daemon import _validate_staging_config; _validate_staging_config(1, '30 days'); print('ok')"
# → ok

env -u VIRTUAL_ENV uv run python -c "from sqlery.core.daemon import _validate_staging_config; _validate_staging_config(30, '30 days')"
# → ValueError: SQLERY_PARTITION_RETENTION=30 days (30 days) must be greater than SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS=30
```

## Known Stubs

None — all routing paths and the promotion function are fully wired. The promotion function requires a live PostgreSQL connection to execute (via `backend.get_raw_cursor()` wired in Phase 16); the advisory-lock + SKIP LOCKED logic is tested with mock cursors.

## Threat Flags

No new security-relevant surface introduced beyond what the plan's threat model covers:
- T-14-03 (Tampering via raw SQL): all table/column references use psycopg parameterized bindings; no f-string SQL interpolation
- T-14-04 (Double-promote race): SKIP LOCKED + pg_try_advisory_lock both applied

## TDD Gate Compliance

Task 1: RED `af00e95` → GREEN `f9e9bf9` — gate compliant  
Task 2: RED `e3de3e9` → GREEN `515f4a3` — gate compliant

## Self-Check: PASSED

- `src/sqlery/django_sqlery/backend.py` — modified (ScheduledJob routing)
- `src/sqlery/django_sqlery/settings.py` — modified (SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS)
- `src/sqlery/core/scheduler.py` — modified (promote_due_scheduled_jobs)
- `src/sqlery/core/daemon.py` — modified (_validate_staging_config + wiring)
- Commits: af00e95, f9e9bf9, e3de3e9, 515f4a3 — all present in git log
