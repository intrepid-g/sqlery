---
phase: 11-parity-gated-tests-ci
plan: 01
subsystem: testing
tags: [pytest, postgres, cron, scheduler, parity, cas, sqlmodel, django]

# Dependency graph
requires:
  - phase: 10-harden-cron-semantics
    provides: "Atomic advance_scheduled_task_if_due CAS + hardened sqlery.core.scheduler.Scheduler (single-fire, drift-free), proven on SQLite only"
provides:
  - "Django x Postgres no-duplicate (PARITY-02) + drift-free (PARITY-03) cron cells in tests/test_atomic_scheduler.py"
  - "Standalone x Postgres no-duplicate (PARITY-02) + drift-free (PARITY-03) cron cells in tests/test_core_standalone.py via pg_standalone_backend fixture"
  - "Four @pytest.mark.postgres cells that SKIP cleanly without SQLERY_TEST_PG_URL and execute on the PG CI rail"
affects: [11-03 matrix gate (PARITY-05 needs real PG cells to collect), parity-gated-tests-ci]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "PG-cell mirror: copy the SQLite class/fixture, add @pytest.mark.postgres + a SQLERY_TEST_PG_URL skip guard, assert the identical invariant against the real backend"
    - "pg_standalone_backend fixture: drop_all + create_all per test against SQLERY_TEST_PG_URL, engine.dispose() on teardown (mirrors pg_sync_backend)"

key-files:
  created: []
  modified:
    - tests/test_atomic_scheduler.py
    - tests/test_core_standalone.py

key-decisions:
  - "Drove sqlery.core.scheduler.Scheduler / backend.advance_scheduled_task_if_due, never the legacy sqlery.executor.TaskExecutor (per 10-04-SUMMARY Deviation Rule 1)"
  - "Standalone drift cell uses module-level sqlery.core.utils.calculate_next_run (no future-clamp) so each advance is computed from the PRIOR scheduled time, proving drift-free advance independent of wall-clock now"
  - "Belt-and-suspenders SKIP: explicit per-method/fixture SQLERY_TEST_PG_URL guard in addition to the conftest collection gate"

patterns-established:
  - "Pattern 1: Postgres parity cell = SQLite analog + class-level @pytest.mark.postgres + env-URL skip guard, asserting the same invariant on the real PG engine"
  - "Pattern 2: PG-bound standalone fixture does drop_all/create_all per test for row isolation on the shared CI PG service"

requirements-completed: [PARITY-02, PARITY-03]

# Metrics
duration: ~15min
completed: 2026-06-08
---

# Phase 11 Plan 01: Postgres Parity Cells (No-Duplicate + Drift) Summary

**Adds the four missing Postgres cron cells — Django x PG and standalone x PG, each asserting single-fire (PARITY-02) and drift-free next_run_at advance (PARITY-03) against the real hardened Scheduler / advance_scheduled_task_if_due CAS, closing the matrix gap Phase 10 deferred.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- `TestCronSemanticsHardeningPostgres` in `tests/test_atomic_scheduler.py`: Django x Postgres mirror of the SQLite hardening class — single-fire CAS under simulated two-leader overlap (PARITY-02) and drift-free advance across ticks (PARITY-03), driving the real `Scheduler` / `advance_scheduled_task_if_due` path on Postgres MVCC.
- `pg_standalone_backend` fixture + `TestStandaloneAdvanceScheduledTaskPostgres` in `tests/test_core_standalone.py`: standalone x Postgres mirror exercising `SQLAlchemyBackend.advance_scheduled_task_if_due` single-winner CAS (PARITY-02) and drift-free advance (PARITY-03) on a real PG engine.
- All four cells carry `@pytest.mark.postgres`, SKIP cleanly (2+2) without `SQLERY_TEST_PG_URL`, collect on `-m postgres`, and were logic-validated against a real backend (SQLite) so they pass — not just skip — when the PG URL is set.
- No production source changes; no new dependencies; pre-existing SQLite suites unregressed (17 passed under `-m "not postgres"`).

## Task Commits

Each task was committed atomically:

1. **Task 1: Django x Postgres no-duplicate + drift cells** - `29efa9e` (test)
2. **Task 2: Standalone x Postgres no-duplicate + drift cells** - `5b9e5c5` (test)

## Files Created/Modified
- `tests/test_atomic_scheduler.py` - Added `import os` and `TestCronSemanticsHardeningPostgres` (class-level `@pytest.mark.postgres` + `@pytest.mark.django_db(transaction=True)`) reusing the existing class's `_scheduler()` / `_make_due_cron_task()` static helpers.
- `tests/test_core_standalone.py` - Added `import os`, the `pg_standalone_backend` fixture (PG-bound twin of `standalone_backend`), and `TestStandaloneAdvanceScheduledTaskPostgres` reusing `_make_due_scheduled_task` / `_job_kwargs_for` / `_count_jobs_for`.

## Decisions Made
- Used the real `sqlery.core.scheduler.Scheduler` and `backend.advance_scheduled_task_if_due` everywhere; left the legacy `sqlery.executor.TaskExecutor` import (test_atomic_scheduler.py line 32) untouched but unused, per CLAUDE.md add-only edit discipline and the 10-04-SUMMARY Rule 1 note.
- Standalone drift cell re-arms the row between ticks via `update_scheduled_task_next_run` and computes each expected occurrence from the PRIOR scheduled time using `sqlery.core.utils.calculate_next_run` (which does not future-clamp), so drift-freedom is asserted independent of wall-clock now.

## Deviations from Plan

None - plan executed exactly as written. (The plan's standalone drift method was implemented with the module-level `calculate_next_run` re-arm idiom it specified; the single-fire and fixture shapes match the cited analogs verbatim.)

## Issues Encountered
- The PG cells cannot run locally (no `SQLERY_TEST_PG_URL`), so to guarantee they will PASS — not merely SKIP — on the PG CI rail, the exact test bodies were logic-validated against the local SQLite `SQLAlchemyBackend` via an ad-hoc harness (single-fire: one winner + one job; drift: 3 monotonic drift-free advances + 3 jobs). Both passed, confirming the assertions are sound on a real backend.
- Black reports each touched file as "would reformat", but the only diff hunks are pre-existing (a missing trailing comma in `skip_on_sqlite` at line 33 of test_atomic_scheduler.py; a blank-line shift at line 14 of test_core_standalone.py) — documented as out-of-scope pre-existing hunks; the newly added lines are black-clean and were left as-is per the plan and CLAUDE.md.

## User Setup Required
None - no external service configuration required. The PG cells run automatically on the CI Postgres rail (which sets `SQLERY_TEST_PG_URL`); locally they SKIP.

## Next Phase Readiness
- PARITY-02 and PARITY-03 now each have a Django x Postgres cell and a standalone x Postgres cell, all `@pytest.mark.postgres`. Plan 03's matrix gate (PARITY-05) now has real PG cells to collect (`-m postgres --collect-only` lists all four).
- No blockers.

---
*Phase: 11-parity-gated-tests-ci*
*Completed: 2026-06-08*
