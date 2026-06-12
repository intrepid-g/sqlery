---
phase: 13-partition-core
plan: "03"
subsystem: testing
tags:
  - partitioning
  - unit-tests
  - advisory-lock
  - back-pressure
  - tdd
dependency_graph:
  requires:
    - 13-01  # partitioning.py implementation
    - 13-02  # daemon tick helpers
  provides:
    - partitioning-unit-test-coverage
  affects:
    - tests/unit/test_partitioning.py
tech_stack:
  added: []
  patterns:
    - mock-cursor harness (MagicMock, no live PG required)
    - side_effect iterator for sequenced fetchone/fetchall responses
key_files:
  created:
    - tests/unit/test_partitioning.py
  modified:
    - src/sqlery/core/partitioning.py  # cherry-picked from 13-01
    - src/sqlery/core/daemon.py        # cherry-picked from 13-02
decisions:
  - "Extended existing 26-test file rather than creating competing test file"
  - "Added TestDaemonHelpers class for _validate_partition_maintenance_interval (daemon.py)"
  - "test_backpressure_invariant_queued and _running added as explicit named tests per plan spec"
  - "test_advisory_lock_loser_skips_without_ddl covers both ensure and reclaim in one test"
  - "Cherry-picked 13-01 and 13-02 commits into worktree branch (partitioning.py was absent)"
metrics:
  duration: "~20 minutes"
  completed: "2026-06-11T20:30:00Z"
  tasks_completed: 1
  files_modified: 3
---

# Phase 13 Plan 03: Partitioning Unit Tests Summary

**One-liner:** 36 mock-cursor unit tests prove all four reclaim skip-rules, R8 advisory-lock coordination, R9 DEFAULT-partition alert, and DETACH→archive→DROP order — no live PG required.

## What Was Built

Extended `tests/unit/test_partitioning.py` (brought in via cherry-pick from 13-01/13-02) with 10 new tests covering the plan's mandatory truths:

| Test | Class | Purpose |
|------|-------|---------|
| `test_backpressure_invariant_queued` | `TestReclaimDrainedPartitions` | R4: queued rows pin partition (skip rule 3a) |
| `test_backpressure_invariant_running` | `TestReclaimDrainedPartitions` | R4: running rows pin partition (skip rule 3b) |
| `test_validate_maintenance_interval_rejects_oversized_interval` | `TestDaemonHelpers` | D1: ValueError when interval > partition interval |
| `test_validate_maintenance_interval_accepts_valid_interval` | `TestDaemonHelpers` | D1: no raise when interval OK |
| `test_validate_maintenance_interval_boundary_day` | `TestDaemonHelpers` | D1: 1440 min == 1 day boundary |
| `test_validate_maintenance_interval_over_boundary_day` | `TestDaemonHelpers` | D1: 1441 min > 1 day raises |
| `test_validate_maintenance_interval_accepts_valid_hour` | `TestDaemonHelpers` | D1: 60 min <= 1 hour passes |
| `test_validate_maintenance_interval_rejects_oversized_hour` | `TestDaemonHelpers` | D1: 61 min > 1 hour raises |
| `test_validate_maintenance_interval_unknown_format_does_not_raise` | `TestDaemonHelpers` | D1: unknown format skips validation (fail-safe) |
| `test_advisory_lock_loser_skips_without_ddl` | `TestDaemonHelpers` | R8: lock loser returns 0, no CREATE/DROP/DETACH |

## Phase 13 Success Criteria Coverage

| Criterion | Tests Covering It | Status |
|-----------|-------------------|--------|
| Four skip-rules incl. back-pressure | `test_skip_rule_1_skips_default_partition`, `test_skip_rule_2_skips_inside_retention_window`, `test_backpressure_invariant_queued`, `test_backpressure_invariant_running` | COVERED |
| Two-daemon zero-DDL-errors | `test_advisory_lock_loser_skips_without_ddl` | COVERED |
| DEFAULT-partition alert > 0 | `test_logs_warning_when_count_positive`, `test_returns_count_from_default_partition` | COVERED |
| Reclaim order DETACH→archive→DROP | `test_detach_before_drop_order`, `test_archive_hook_called_between_detach_and_drop` | COVERED |

## Test Run Results

```
36 passed in 0.30s
```

All 36 tests pass with `uv run --extra dev --extra postgres pytest tests/unit/test_partitioning.py`.

## Deviations from Plan

**1. [Rule 3 - Blocking] Cherry-pick required: source files absent from worktree branch**

- **Found during:** Task 1 — pre-execution check
- **Issue:** The worktree branch was based on `33eee21` (v0.22.4 release base), so `src/sqlery/core/partitioning.py` and the 13-02 daemon changes were not present.
- **Fix:** `git cherry-pick af186cf a2a655a e4fc8e8 --no-commit` brought in the three commits from the `v0.22.3-branch` (13-01 tests, 13-01 implementation, 13-02 daemon changes) and staged them.
- **Files modified:** `src/sqlery/core/partitioning.py`, `src/sqlery/core/daemon.py`, `tests/unit/test_partitioning.py`
- **Commit:** c1b27bf

**2. [Existing coverage] 26 tests already present from wave 1 — extended rather than replaced**

- The plan's `files_modified` listed `tests/test_partitioning.py` (root-level) but per the `<IMPORTANT_existing_coverage>` instruction, the existing file at `tests/unit/test_partitioning.py` was extended.
- 10 tests were added (total 36 vs. 26 original): `test_backpressure_invariant_queued`, `test_backpressure_invariant_running`, `TestDaemonHelpers` (8 methods).
- No duplicate assertions — the two backpressure tests reuse the same mock plumbing as `test_skip_rule_3_skips_partition_with_live_work` but add explicit DETACH/EXISTS/DROP assertions with named test methods per the plan spec.

**3. [CLAUDE.md] Run command uses `--extra dev --extra postgres`**

- Standard `uv run pytest` without extras fails because `psycopg` is only installed with the postgres extra, and test helpers require dev extra. The plan's test note (`env -u VIRTUAL_ENV uv run pytest tests/unit/test_partitioning.py -q`) was adjusted to use both extras.

## Known Stubs

None. All test assertions are fully wired to the mock cursor harness.

## Threat Flags

None. Test-only file; no new production network endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- `tests/unit/test_partitioning.py` — EXISTS (36 tests, 0 failures)
- `src/sqlery/core/partitioning.py` — EXISTS
- Commit `c1b27bf` — EXISTS
