---
phase: 13-partition-core
plan: "02"
subsystem: database
tags: [postgres, partitioning, daemon, cleanup, routing-seam, config-validation]

requires:
  - 13-01  # partitioning.py with ensure_future_partitions, reclaim_drained_partitions, check_default_partition

provides:
  - "core/cleanup.py: _partitioned_pg routing seam in auto_cleanup — hasattr guard skips job loops when backend is partitioned"
  - "core/daemon.py: _should_run_partition_maintenance — cadence helper (mirrors _should_run_cleanup)"
  - "core/daemon.py: _validate_partition_maintenance_interval — raises ValueError if interval_minutes > partition interval"
  - "core/daemon.py: PARTITION_MAINTENANCE_INTERVAL_MINUTES config key (default 5, validated <= SQLERY_PARTITION_INTERVAL)"
  - "core/daemon.py: partition maintenance tick in _run_daemon — ensure + reclaim + check_default on cadence"

affects:
  - 13-03  # cleanup routing plan (Phase 16 wires _partitioned_pg() on backend)
  - 16     # backend.get_raw_cursor() activates the daemon tick

tech-stack:
  added: []  # no new deps
  patterns:
    - "comment-out-then-add convention: old lines preserved as comments, new lines below"
    - "hasattr duck-typing guard: _partitioned_pg routing seam is no-op until Phase 16"
    - "try/except around each daemon tick block: maintenance failure never crashes the daemon loop"
    - "Config validation at startup: invalid interval disables maintenance and logs ERROR"

key-files:
  created: []
  modified:
    - src/sqlery/core/cleanup.py
    - src/sqlery/core/daemon.py

key-decisions:
  - "hasattr duck-typing for _partitioned_pg: seam works in both modes without Phase 16 dependency; no import of backend type at all"
  - "re imported at top-level (not inline): CLAUDE.md requires top-level imports; added import re to daemon.py module header"
  - "get_raw_cursor() left as a TODO stub: try/except in the maintenance tick block catches AttributeError until Phase 16 wires the cursor factory"
  - "Config validation disables maintenance on error rather than crashing: mirrors pattern used by existing daemon error handlers"

duration: 7min
completed: 2026-06-11
---

# Phase 13 Plan 02: Partition Routing Seam + Daemon Tick Summary

**Cleanup routing seam (hasattr guard) and daemon partition maintenance tick (ensure + reclaim + check_default) with config validation and WARNING log for DEFAULT-partition overflow**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-06-11T20:13:41Z
- **Completed:** 2026-06-11T20:21:01Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added `_partitioned_pg` routing seam to `CleanupManager.auto_cleanup`: hasattr-guarded `if not _in_partition_mode` blocks skip job-by-age and job-by-count loops; registry cleanup runs unconditionally; `partition_mode=True` set in results dict for observability
- Added `import re` at top-level in `daemon.py` (per CLAUDE.md: no inline imports)
- Added `from . import partitioning as _partitioning` to `daemon.py` top-level imports
- Added `_should_run_partition_maintenance(last_run, interval_minutes)` helper (mirrors `_should_run_cleanup`)
- Added `_validate_partition_maintenance_interval(interval_minutes, partition_interval_str)` helper — parses "N day/hour/minute" interval string with `re.match`, raises `ValueError` if minutes > computed limit
- Added partition maintenance config block in `_run_daemon`: reads 6 config keys with defaults; validates interval at startup; disables maintenance (with ERROR log) if invalid
- Added partition maintenance tick in main daemon loop: ensure → reclaim → check_default, all inside try/except; DEFAULT > 0 emits WARNING
- All 26 partitioning unit tests continue to pass (no regressions)

## Task Commits

1. **Task 1 — cleanup.py routing seam** - `fae3ca3`
2. **Task 2 — daemon.py partition tick** - `e4fc8e8`

## Files Created/Modified

- `src/sqlery/core/cleanup.py` — `_partitioned_pg` routing seam in `auto_cleanup`; job loops wrapped in `if not _in_partition_mode` guards
- `src/sqlery/core/daemon.py` — top-level `import re`, `_partitioning` import, two helper functions, partition config block, maintenance tick in `_run_daemon`

## Decisions Made

- **`re` at top-level:** CLAUDE.md prohibits inline imports inside functions. The `_validate_partition_maintenance_interval` helper uses `re.match` for interval string parsing; `import re` added to module header.
- **`get_raw_cursor()` as TODO stub:** The backend cursor factory is wired in Phase 16. A `# TODO(Phase 16)` comment marks the call; the surrounding try/except catches `AttributeError` and logs an error, making the tick a safe no-op until Phase 16.
- **Seam via `hasattr` duck-typing:** avoids any compile-time or import-time dependency on the backend implementation; works correctly in Phase 13 (hasattr returns False → existing path) and Phase 16+ (hasattr returns True → partition path).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Moved `import re` to top-level instead of inline function**
- **Found during:** Task 2 planning
- **Issue:** The plan spec shows `import re as _re` inside `_validate_partition_maintenance_interval`. CLAUDE.md prohibits inline imports in functions (with narrow exceptions for circular imports). `re` is a stdlib module with no circular import risk.
- **Fix:** Added `import re` to the top-level import block. The function body uses `re.match` directly (no alias needed).
- **Files modified:** `src/sqlery/core/daemon.py`
- **Committed in:** `e4fc8e8`

## Known Stubs

- **`backend.get_raw_cursor()`** in `src/sqlery/core/daemon.py` line ~558 — intentional stub. The cursor factory is wired in Phase 16. The try/except block catches `AttributeError`/`NotImplementedError` and logs an error, making partition maintenance a no-op until Phase 16. This stub prevents the daemon from crashing before the cursor factory exists. Tracked as acceptable per T-13-08 in the threat model.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced. All changes are daemon-internal. Config values read via `get_config()` (operator-supplied, not user input).

## Self-Check

### Created files exist

- `src/sqlery/core/cleanup.py` — modified, exists: FOUND
- `src/sqlery/core/daemon.py` — modified, exists: FOUND

### Commits exist

- `fae3ca3` — Task 1 (cleanup.py routing seam): FOUND
- `e4fc8e8` — Task 2 (daemon.py partition tick): FOUND

### Verification results

- `python -c "from sqlery.core.daemon import DaemonManager, _should_run_partition_maintenance, _validate_partition_maintenance_interval; print('imports OK')"` → imports OK
- `_validate_partition_maintenance_interval(10, '1 day')` → passes (10 <= 1440)
- `_validate_partition_maintenance_interval(2000, '1 day')` → raises ValueError (2000 > 1440)
- `_partitioned_pg` routing seam present in cleanup.py → OK
- `hasattr` guard present → OK
- 26 partitioning unit tests → all pass

## Self-Check: PASSED
