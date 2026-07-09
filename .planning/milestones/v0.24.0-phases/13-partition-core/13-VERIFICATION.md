---
phase: 13-partition-core
verified: 2026-06-11T20:36:58Z
status: passed
score: 6/6 must-haves verified
overrides_applied: 0
---

# Phase 13: partition-core Verification Report

**Phase Goal:** Partition maintenance machinery exists and is safe — future partitions provisioned ahead, drained partitions reclaimed by DROP under the back-pressure invariant, all DDL coordinated across daemons. Pure new code + daemon wiring (activates only when partitioned in Phase 15).
**Verified:** 2026-06-11T20:36:58Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1 | Unit tests prove the four reclaim skip-rules including the back-pressure invariant (skip DEFAULT, skip inside retention, skip partitions with queued/running rows, advisory-lock-not-acquired) | VERIFIED | `test_skip_rule_1_skips_default_partition`, `test_skip_rule_2_skips_inside_retention_window`, `test_skip_rule_3_skips_partition_with_live_work`, `test_backpressure_invariant_queued`, `test_backpressure_invariant_running`, `test_returns_zero_if_advisory_lock_not_acquired` — all pass in TestReclaimDrainedPartitions. The implementation at partitioning.py:240-251 checks `status IN ('queued', 'running')` and skips on `True`. |
| 2 | Two concurrent daemons cause zero DDL errors (advisory-lock coordination) | VERIFIED | `test_advisory_lock_loser_skips_without_ddl` (TestDaemonHelpers:678) passes — asserts result==0 and that CREATE/DROP/DETACH never appear in SQL calls when fetchone returns `(False,)`. Both `ensure_future_partitions` and `reclaim_drained_partitions` implement `pg_try_advisory_lock` guards with `finally` blocks for `pg_advisory_unlock` (partitioning.py:139-188 and :219-280). |
| 3 | DEFAULT-partition row count is exposed and alerts when > 0 | VERIFIED | `check_default_partition` returns the count and calls `logger.warning(...)` when `count > 0` (partitioning.py:329-335). Covered by `test_logs_warning_when_count_positive` and `test_returns_count_from_default_partition` (both pass). The daemon tick also emits a WARNING at daemon.py:575-578 when `default_count > 0`. |
| 4 | Reference behavior matches pgwq.sql: reclaim order is DETACH → archive hook → DROP; ensure_future_partitions catches the attach-conflict error and alerts instead of wedging the loop | VERIFIED | `test_detach_before_drop_order` asserts detach_idx < drop_idx (both found in SQL calls). `test_archive_hook_called_between_detach_and_drop` asserts DETACH < hook < DROP call order. `test_catches_attach_conflict_and_continues` verifies that a `psycopg.errors.InvalidTableDefinition` raise does not propagate. Source at partitioning.py:254-275 confirms the order; lines 172-186 catch `psycopg.DatabaseError` and `continue`. |
| 5 | core/partitioning.py has NO Django/SQLAlchemy module-level imports (framework-agnostic) | VERIFIED | AST scan confirms zero ORM imports at col_offset==0. Module-level imports are: `logging`, `re`, `datetime/timezone`, `psycopg`, `psycopg.sql`. `test_no_orm_imports_at_module_level` passes as part of the 36-test run. |
| 6 | PARTITION_MAINTENANCE_INTERVAL_MINUTES default 5 with validation invariant (≤ partition interval); D1 config defaults match | VERIFIED | daemon.py:436 `get_config("PARTITION_MAINTENANCE_INTERVAL_MINUTES", 5)`. Defaults: `SQLERY_PARTITION_INTERVAL="1 day"` (437), `SQLERY_PARTITION_RETENTION="30 days"` (438), `SQLERY_PARTITION_PREMAKE=7` (439). `_validate_partition_maintenance_interval` raises `ValueError` when `interval_minutes > partition_interval_minutes`; disables maintenance on error rather than crashing (449-451). Six `TestDaemonHelpers` validation tests pass including boundary cases. |

**Score:** 6/6 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/sqlery/core/partitioning.py` | Framework-agnostic partition maintenance module | VERIFIED | 338 lines, 5 public/private callables, zero ORM imports, two advisory-lock int64 constants |
| `tests/unit/test_partitioning.py` | 36 unit tests (mock-cursor, no live DB) | VERIFIED | 36 tests across 6 classes; all pass with `uv run --extra dev --extra postgres pytest` |
| `src/sqlery/core/daemon.py` | Partition tick + config validation + advisory-lock wiring | VERIFIED | `_should_run_partition_maintenance`, `_validate_partition_maintenance_interval`, partition config block at line 434-453, maintenance tick at line 550-582 |
| `src/sqlery/core/cleanup.py` | `_partitioned_pg` routing seam | VERIFIED | hasattr guard at cleanup.py:203-212; job-by-age and job-by-count loops wrapped in `if not _in_partition_mode`; registry cleanup runs unconditionally |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `daemon.py` | `partitioning.ensure_future_partitions` | `from . import partitioning as _partitioning` at line 18; called at line 561 | WIRED | Called inside cadence-gated try/except block |
| `daemon.py` | `partitioning.reclaim_drained_partitions` | Same import; called at line 564 | WIRED | Called with all config values including optional archive_hook |
| `daemon.py` | `partitioning.check_default_partition` | Same import; called at line 567 | WIRED | DEFAULT count logged as WARNING at line 575-578 |
| `daemon.py` | `_validate_partition_maintenance_interval` | Module-level function; called at line 446 | WIRED | Called at startup; disables maintenance on ValueError |
| `cleanup.py` | `_partitioned_pg` routing seam | `hasattr(self.backend, '_partitioned_pg')` duck-typing guard | WIRED | Seam is a no-op until Phase 16; safe for current phase |

### Data-Flow Trace (Level 4)

Not applicable — `partitioning.py` is a library module (takes a raw cursor, produces DDL effects and return counts). No component renders dynamic data from a store. The daemon tick is a non-rendering background loop. Data flows through function arguments, not state/props.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 36 partitioning tests pass | `uv run --extra dev --extra postgres pytest tests/unit/test_partitioning.py -v` | 36 passed in 0.24s | PASS |
| No regressions in unit suite | `uv run --extra dev --extra postgres pytest tests/unit/ -q` | 460 passed, 11 skipped, 3 xfailed, 0 failures | PASS |
| AST check: no ORM imports at module level | Python AST scan of partitioning.py | No Django/SQLAlchemy/SQLModel at col_offset==0 | PASS |
| `_validate_partition_maintenance_interval` importable | Confirmed via test execution | Imported in TestDaemonHelpers tests | PASS |

### Probe Execution

No probe scripts declared for this phase. Step 7c: SKIPPED (no `scripts/*/tests/probe-*.sh` files for phase 13).

### Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| R3 (REQ-partition-drop-reclaim, core half) | Core reclaim machinery — DETACH + DROP with skip rules | SATISFIED | `reclaim_drained_partitions` implements full reclaim path; daemon wires it |
| R4 (REQ-backpressure-invariant) | Partitions with queued/running rows are never dropped | SATISFIED | `WHERE status IN ('queued', 'running')` EXISTS check; two explicit backpressure tests |
| R8 (REQ-advisory-lock-coordination) | Concurrent daemons skip tick on lock-loss, zero DDL conflicts | SATISFIED | `pg_try_advisory_lock` + `finally pg_advisory_unlock` in both functions; `test_advisory_lock_loser_skips_without_ddl` |
| R9 (REQ-operator-metrics, DEFAULT-partition alert) | DEFAULT partition row count exposed + WARNING log | SATISFIED | `check_default_partition` returns count, logs WARNING when >0; daemon also logs WARNING |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/sqlery/core/daemon.py` | 558 | `TODO(Phase 16): backend.get_raw_cursor() is wired in Phase 16` | INFO | Intentional known stub; references a specific future phase (Phase 16 is in ROADMAP.md at line 89). The surrounding try/except catches `AttributeError`/`NotImplementedError`, making partition maintenance a no-op until Phase 16 — this is the correct safe behavior. Not a blocker per debt-marker gate (references formal follow-up work). |

No `TBD`, `FIXME`, or `XXX` markers found in any phase-13-modified file.

### Human Verification Required

None. All success criteria are programmatically verifiable via unit tests and static analysis.

### Gaps Summary

No gaps. All six must-have truths are VERIFIED against the actual source code and confirmed by passing tests.

**Notable observation:** The tests require `uv run --extra dev --extra postgres pytest` because `psycopg` is in the `postgres` extra. Running with the base venv produces `ModuleNotFoundError: No module named 'psycopg'`. This is expected and documented in the SUMMARY files — it is not a gap, as the `postgres` extra is the correct environment for these tests. The test_status note in the phase objective confirms "36 passed" with the correct invocation.

---

_Verified: 2026-06-11T20:36:58Z_
_Verifier: Claude (gsd-verifier)_
