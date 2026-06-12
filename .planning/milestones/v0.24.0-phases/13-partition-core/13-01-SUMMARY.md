---
phase: 13-partition-core
plan: "01"
subsystem: database
tags: [postgres, partitioning, advisory-lock, psycopg, ddl, partition-maintenance]

requires: []

provides:
  - "core/partitioning.py: framework-agnostic PostgreSQL partition maintenance module"
  - "ensure_future_partitions: creates next N+1 daily RANGE partitions with advisory lock guard and attach-conflict handling"
  - "reclaim_drained_partitions: four skip rules (DEFAULT, retention window, live work, lock) + DETACH→archive→DROP order"
  - "check_default_partition: returns row count and logs WARNING when > 0"
  - "_list_partitions: reads pg_inherits + pg_get_expr, returns (name, upper_bound|None)"
  - "ADVISORY_LOCK_ENSURE, ADVISORY_LOCK_RECLAIM: stable int64 constants from ASCII bytes"

affects:
  - 13-02  # daemon plan wires ensure+reclaim on 5-minute cadence
  - 13-03  # cleanup routing plan uses _partitioned_pg() helper
  - 16     # backend cleanup_jobs routing completes metric set

tech-stack:
  added: []  # no new deps; uses existing psycopg (>=3.1)
  patterns:
    - "Raw-cursor module pattern: core/ logic takes a psycopg cursor, zero ORM imports at module level"
    - "pg_try_advisory_lock guard: skip-tick-on-lock-loss pattern for concurrent daemon safety"
    - "DETACH PARTITION before DROP: shrinks DDL lock window, enables archive hook callback"
    - "Back-pressure invariant: EXISTS(queued/running) check pins a partition regardless of age"

key-files:
  created:
    - src/sqlery/core/partitioning.py
    - tests/unit/test_partitioning.py
  modified: []

key-decisions:
  - "Advisory-lock keys derived from ASCII bytes of 8-char tags (SQLEPART, SQLERCLA) — stable, documented, distinct int64s fitting signed int8 range"
  - "Narrow exception catch in ensure_future_partitions: psycopg.DatabaseError only; RuntimeError and others re-raise (ensuring advisory lock is released via finally)"
  - "Cutoff computed by one DB round-trip (SELECT now() - interval) rather than Python datetime math, keeping timezone handling server-authoritative"

patterns-established:
  - "core/ modules: no Django/SQLAlchemy/SQLModel at module level — raw cursor only"
  - "DETACH → archive_hook → DROP reclaim order for all partition drop operations"
  - "Advisory lock guard on every DDL function: return 0 immediately if lock not acquired"

requirements-completed: [R3, R4, R8, R9]

duration: 9min
completed: 2026-06-11
---

# Phase 13 Plan 01: Partition Core Summary

**Framework-agnostic partition maintenance module with advisory-lock-guarded DDL, four reclaim skip rules including back-pressure invariant, and DEFAULT-partition alert**

## Performance

- **Duration:** 9 min
- **Started:** 2026-06-11T20:04:37Z
- **Completed:** 2026-06-11T20:13:41Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- Created `src/sqlery/core/partitioning.py` — ~190 lines, zero ORM imports, all four public/private callables implemented
- 26 unit tests covering all four functions, all three advisory-lock behaviors, all four skip rules, and archive-hook exception isolation
- All plan success criteria verified: importable, AST-clean, constants defined, Identifier used throughout

## Task Commits

TDD task — three commits:

1. **RED — Failing tests** - `af186cf` (test)
2. **GREEN — Implementation** - `a2a655a` (feat)

## Files Created/Modified

- `src/sqlery/core/partitioning.py` — New module: `_list_partitions`, `ensure_future_partitions`, `reclaim_drained_partitions`, `check_default_partition`, `ADVISORY_LOCK_ENSURE`, `ADVISORY_LOCK_RECLAIM`
- `tests/unit/test_partitioning.py` — 26 unit tests, mock-cursor-only (no live DB)

## Decisions Made

- **Exception narrowing in `ensure_future_partitions`:** The spec says "catch psycopg.DatabaseError or both" — implemented exactly that. `RuntimeError` and non-DB exceptions re-raise, so the `finally` advisory-lock release fires, and the caller learns about unexpected errors. `psycopg.DatabaseError` (attach-conflict family) is caught, logged as WARNING, and the loop continues.
- **Advisory-lock key derivation:** `int.from_bytes(b"SQLEPART", "big")` = 6077516534999474260, `int.from_bytes(b"SQLERCLA", "big")` = 6077516534985552193. Both are positive, fit in signed int64, and are documented in the module docstring.
- **Cutoff via DB query:** `SELECT now() - %s::interval` keeps timezone handling server-authoritative rather than mixing Python's `datetime.now(utc)` with PostgreSQL interval arithmetic.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added top-level `import psycopg` for DatabaseError isinstance check**
- **Found during:** Task 1, GREEN phase
- **Issue:** The plan spec said to catch `psycopg.DatabaseError`; originally used broad `except Exception` which swallowed `RuntimeError` (preventing the advisory-lock-on-error test from working). CLAUDE.md prohibits inline imports inside functions.
- **Fix:** Added `import psycopg` at top of file (alongside existing `from psycopg import sql as pgsql`); narrowed the exception catch to check `isinstance(exc, psycopg.DatabaseError)` before deciding whether to re-raise or log+continue.
- **Files modified:** `src/sqlery/core/partitioning.py`
- **Verification:** All 26 tests pass including `test_advisory_lock_released_even_on_error`
- **Committed in:** `a2a655a`

**2. [Rule 3 - Blocking] Fixed test mock sequences to match implementation's actual fetchone call pattern**
- **Found during:** Task 1, GREEN phase (test helper mismatch)
- **Issue:** Test mock helpers provided insufficient fetchone responses (didn't account for the `SELECT now() - interval` cutoff query in `reclaim_drained_partitions` or the two-query pattern per iteration in `ensure_future_partitions`).
- **Fix:** Rewrote `_make_ensure_cursor` and `_make_reclaim_cursor` helpers to provide the full `(lock,) + (cutoff,) + [(live_work,...)]` and `(lock,) + [(lo, hi), ...]` sequences.
- **Files modified:** `tests/unit/test_partitioning.py`
- **Verification:** All 26 tests pass
- **Committed in:** `a2a655a`

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 blocking test fix)
**Impact on plan:** Both fixes essential for correctness and test validity. No scope creep.

## Issues Encountered

- Worktree venv lacked psycopg on first run — resolved by `uv sync --extra dev --extra postgres`. The worktree has its own `.venv` with the package installed as an editable pointing to the worktree's `src/`.
- First pytest run picked up `python3.12` from a sibling project's venv (stale `VIRTUAL_ENV`); the `env -u VIRTUAL_ENV` prefix correctly isolated the worktree's `python3.13` venv on subsequent runs.

## Known Stubs

None — all four callables are fully implemented with no placeholder returns.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced. All SQL identifier interpolation uses `pgsql.Identifier` per T-13-01. Advisory lock keys are distinct constants per T-13-03. Archive-hook exceptions are isolated per T-13-04. Status literals are constants per T-13-05.

## Next Phase Readiness

- `core/partitioning.py` is ready for Plan 02 (`core/daemon.py`) to wire `ensure_future_partitions` + `reclaim_drained_partitions` on a 5-minute cadence
- No blockers

---
*Phase: 13-partition-core*
*Completed: 2026-06-11*
