---
phase: 11-parity-gated-tests-ci
plan: 02
subsystem: testing
tags: [pytest, postgres, leases, scheduler-election, failover, parity-matrix]

# Dependency graph
requires:
  - phase: 08-standalone-lease-parity
    provides: standalone SQLAlchemyBackend queue-lease primitives (claim/renew/release)
  - phase: 09-core-shared-scheduler-election
    provides: WorkerProcess.run per-queue election lifecycle + run_due_tasks(queue_names=held)
provides:
  - PARITY-01 failover proofs (SQLite in-process + real-backend Postgres) for the Django/active path
  - PARITY-01 standalone half — real-backend lease takeover on the standalone SQLAlchemyBackend (Postgres)
  - PARITY-04 bare-worker E2E proofs (SQLite in-process + real no-Django standalone subprocess)
affects: [11-03, ci-matrix, parity-gated-tests]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "(integration, db) parity axis with @pytest.mark.postgres on the postgres pytest.param only"
    - "Failover simulated via PAST expires_at write — never a real-TTL sleep"
    - "Bare-worker E2E proven by enqueued-QueuedJob count (==1), not by inspecting post-shutdown leases"
    - "no-Django subprocess election needs worker_module.close_old_connections patched to None"

key-files:
  created:
    - tests/test_parity_scheduler.py
  modified:
    - tests/chaos/test_lease_zombie.py

key-decisions:
  - "Imported the proven election harness (_run_one_election_cycle/_seed_due_task/_job_count_for_task/_claimed_queues) from tests.unit.test_worker rather than re-deriving it, to avoid duplication"
  - "Standalone E2E subprocess no-ops worker_module.close_old_connections (real Django fn raises ImproperlyConfigured with no settings, which the worker's broad except would spin on)"
  - "PG failover cell forces lease expiry via a PAST expires_at write (Django ORM update / backend session) instead of a real sleep, matching the 11-PATTERNS convention"

patterns-established:
  - "Parity matrix cell: SQLite cell unmarked (default rail) + Postgres cell @pytest.mark.postgres (PG rail, auto-skip without SQLERY_TEST_PG_URL)"
  - "Real-backend lease takeover proof: claim from daemon-a, write PAST expires_at, claim from daemon-b, assert daemon-b owns the row"

requirements-completed: [PARITY-01, PARITY-04]

# Metrics
duration: ~30min
completed: 2026-06-08
---

# Phase 11 Plan 02: Failover + Bare-Worker Parity Cells Summary

**Cross-matrix PARITY-01 (failover) and PARITY-04 (bare-worker E2E) proofs: SQLite in-process election cells plus real-backend Postgres lease-takeover cells for both the Django/active and standalone paths, with no production source changes.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-06-08T11:10:00Z (approx)
- **Completed:** 2026-06-08T11:39:34Z
- **Tasks:** 2
- **Files modified:** 2 (1 created, 1 extended)

## Accomplishments
- New `tests/test_parity_scheduler.py` with `TestParityFailover` (PARITY-01) and `TestParityBareWorkerE2E` (PARITY-04), each pairing a fast SQLite in-process cell with a Postgres/real-process cell.
- SQLite failover cell drives the real `WorkerProcess.run` election against `FakeBackend` with a PAST `expires_at` (dead leader) and asserts takeover via the claim record + the single enqueued cron job.
- Postgres failover cell proves real Django-backend lease takeover: `daemon-a` claims, expires (PAST `expires_at` write), `daemon-b` re-claims and owns the row.
- Bare-worker standalone E2E drives a real no-Django subprocess (`slow`) that constructs only a `WorkerProcess` (no daemon), self-elects, and fires a due cron — proven by `JOB_COUNT=1`.
- Extended `tests/chaos/test_lease_zombie.py` with `TestStandaloneLeaseFailoverPostgres` + a `pg_standalone_backend` fixture, proving expired-lease takeover on the standalone `SQLAlchemyBackend` against real Postgres (PARITY-01 standalone half).
- All Postgres cells skip cleanly without `SQLERY_TEST_PG_URL`; no real-TTL sleeps on the default rail (SQLite cells run in ~0.5s).

## Task Commits

Each task was committed atomically:

1. **Task 1: New tests/test_parity_scheduler.py — failover + bare-worker (PARITY-01, PARITY-04)** - `ac15635` (test)
2. **Task 2: Standalone-backend real-lease failover PG cell (PARITY-01 standalone half)** - `950d275` (test)

## Files Created/Modified
- `tests/test_parity_scheduler.py` (created, 285 lines) - PARITY-01 + PARITY-04 cells parametrized over the `(integration, db)` axis with the PG-only marker; reuses the Phase 9 election harness.
- `tests/chaos/test_lease_zombie.py` (modified, +~109 lines) - added `pg_standalone_backend` fixture and `TestStandaloneLeaseFailoverPostgres` proving standalone-backend lease takeover on Postgres.

## Decisions Made
- Imported the election harness from `tests.unit.test_worker` (importable without circular issues) rather than copying it, keeping a single source of truth for `_run_one_election_cycle` et al.
- Forced lease expiry via PAST `expires_at` writes (Django ORM `.update()` for the active backend; backend-session row write for the standalone backend) in all real-backend cells — no real-TTL sleeps.
- Marked the standalone real-process bare-worker cell `slow` (it shells out via `_run_no_django`); the SQLite in-process cells stay on the fast default rail.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] no-Django subprocess election spun forever on Django `close_old_connections`**
- **Found during:** Task 1 (standalone bare-worker E2E cell)
- **Issue:** Django IS installed in the dev env, so `worker_module.close_old_connections` resolves to the real Django function. In the no-Django standalone subprocess (`DJANGO_SETTINGS_MODULE` scrubbed) it raises `ImproperlyConfigured` at the top of every `WorkerProcess.run` loop iteration. The worker's broad `except Exception` then `continue`d (with `time.sleep` no-op'd), producing an infinite tight loop that hit the 60s subprocess timeout — the cell could never reach the bounded one-pass exit.
- **Fix:** In the subprocess script, set `worker_module.close_old_connections = None` (the bare standalone worker has no Django connections to prune), mirroring exactly what `_run_one_election_cycle` does for the in-process cells (test_worker.py 384-385).
- **Files modified:** tests/test_parity_scheduler.py
- **Verification:** Ran the script standalone (`JOB_COUNT=1`) and via pytest `-m "slow and not postgres"` (1 passed in 0.70s).
- **Committed in:** ac15635 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** The fix was required for the standalone E2E cell to terminate; it is test-harness-only and matches the established in-process election pattern. No scope creep, no production source change.

## Issues Encountered
- An observed `Heartbeat failed: ... unexpected keyword argument 'total_busy_second...'` warning from the standalone backend's `update_worker_heartbeat` appears in subprocess stderr but is caught internally by the worker and does not affect cron firing or the `JOB_COUNT=1` assertion. It is a pre-existing signature divergence in the standalone heartbeat path, out of scope for this tests-only plan (logged here, not fixed).

## User Setup Required
None - no external service configuration required. (Postgres cells require `SQLERY_TEST_PG_URL` to actually run; they skip cleanly otherwise.)

## Next Phase Readiness
- PARITY-01 and PARITY-04 now have CI-gated cross-backend proofs. The PG cells are collected by `-m postgres` (no path filter on the PG rail), so they run on the dedicated Postgres CI rail once `SQLERY_TEST_PG_URL` is set.
- Remaining Phase 11 work (PARITY-02/03 PG cells in `test_atomic_scheduler.py`, the CI matrix gate in `.github/workflows/test.yml`) is unaffected by these additions.

## Self-Check: PASSED

- FOUND: tests/test_parity_scheduler.py
- FOUND: tests/chaos/test_lease_zombie.py (extended)
- FOUND: .planning/phases/11-parity-gated-tests-ci/11-02-SUMMARY.md
- FOUND commit: ac15635 (Task 1)
- FOUND commit: 950d275 (Task 2)

---
*Phase: 11-parity-gated-tests-ci*
*Completed: 2026-06-08*
