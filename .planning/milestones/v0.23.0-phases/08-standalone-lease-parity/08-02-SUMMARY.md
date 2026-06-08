---
phase: 08-standalone-lease-parity
plan: 02
subsystem: standalone-backend
tags: [leases, leader-election, sqlalchemy, sqlite, postgres, cas, atomic-claim]

# Dependency graph
requires:
  - "08-01: DaemonLease SQLModel (sqlery_daemon_lease) + version CAS column"
provides:
  - "Real SQLAlchemyBackend.claim_queue_leases / renew_queue_leases / release_queue_leases (replaces ABC fake-election default)"
  - "Private _claim_one_lease per-queue atomic helper (Postgres FOR UPDATE SKIP LOCKED + SQLite version-CAS)"
  - "SQLite lease lifecycle test suite mirroring the FakeBackend contract in test_daemon.py"
affects: [standalone-leader-election, fastapi-backend, daemon-scheduling]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dialect-split atomic lease claim: Postgres with_for_update(skip_locked=True) vs SQLite version-CAS update with rowcount==1 success check (mirrors claim_job)"
    - "Naive/aware datetime normalization for SQLite-read columns before Python comparison (dt if dt.tzinfo else dt.replace(tzinfo=UTC))"
    - "synchronize_session=False on ORM update() whose WHERE compares a naive DB column to an aware value (skips the ORM evaluator)"

key-files:
  created: []
  modified:
    - "src/sqlery/fastapi_sqlery/backend.py"
    - "tests/unit/test_sqlalchemy_backend_sync.py"

key-decisions:
  - "Took over expired leases via version-CAS with synchronize_session=False so the SQLite naive expires_at column is compared in raw SQL, not the ORM evaluator (which raised TypeError on naive-vs-aware)."
  - "Normalized read-back expires_at to UTC-aware before the Python expired/live check, matching the existing dt-normalization pattern already in backend.py."
  - "Kept the redundant expires_at < now guard in the CAS WHERE (as the plan specified) alongside the version CAS — both contribute to single-winner atomicity."
  - "Committed the tz/evaluator fix in the Task 2 commit because the bug only surfaced when the new lease tests exercised the SQLite expired-take-over path."

patterns-established:
  - "Standalone lease parity: dialect-correct atomic per-queue claim matching DjangoBackend semantics, proven by a SQLite mirror of the FakeBackend lease contract."

requirements-completed: [LEASE-03, LEASE-04, LEASE-05]

# Metrics
duration: ~18min
completed: 2026-06-08
---

# Phase 8 Plan 02: Standalone Lease Backend Summary

**SQLAlchemyBackend now implements real per-queue lease claiming (`claim_queue_leases` / `renew_queue_leases` / `release_queue_leases`) with Postgres `FOR UPDATE SKIP LOCKED` and SQLite version-CAS take-over, replacing the inherited ABC fake-election default, and a SQLite test suite proves the full lease lifecycle at parity with the Django/FakeBackend contract.**

## Performance

- **Duration:** ~18 min
- **Tasks:** 2
- **Files modified:** 2 (both modified, 0 created)

## Accomplishments
- Added `DaemonLease` and `IntegrityError` to the module-level imports in `backend.py` (no inline imports; reused the already-present `update`, `delete`, `select`, `and_`, `datetime`, `timedelta`, `UTC`).
- Implemented `claim_queue_leases` on `SQLAlchemyBackend`: opens one session, computes `determine_claim_strategy(dialect)` (verbatim reuse), loops the requested queues, and delegates each to a private `_claim_one_lease`, returning the claimed subset — mirroring the Django per-queue loop.
- Implemented `_claim_one_lease` with a dialect split:
  - **Postgres (`skip_locked`):** `select(DaemonLease).with_for_update(skip_locked=True)`; insert if free, take over if expired or already ours, never steal a live foreign lease; `IntegrityError` on the insert race → return `False`.
  - **SQLite/fallback (`optimistic_version`):** read row; fresh insert (with `IntegrityError` rollback → `False`); idempotent re-claim of our own lease via version-CAS; expired take-over via `update(...).where(version==current).where(expires_at<now)` with `rowcount == 1` success — the exact CAS shape from `claim_job`.
- Implemented `renew_queue_leases` (bulk `update` filtered on `queue_name in owned AND daemon_id == self`) and `release_queue_leases` (bulk `delete` with the same owner filter) — non-owner renew/release is a no-op, matching Django.
- Added `TestSQLAlchemyLeaseLifecycle` to `test_sqlalchemy_backend_sync.py` covering every behavior the FakeBackend contract asserts: free-claim insert, skip-live-foreign, expired-reclaim (owner updated, still one row), idempotent self re-claim, renew-extends, renew/release non-owner no-op, release-deletes-owned, a concurrent-claim single-winner thread race, and the daemon-call-contract pin (LEASE-05).
- Confirmed `src/sqlery/core/daemon.py` is unchanged; its existing `claim/renew/release` calls (`daemon.py:363/407/413/510`, `lease_secs = check_interval * 3`) now run against real `DaemonLease` rows.

## Task Commits

1. **Task 1: Implement SQLAlchemyBackend lease methods (claim/renew/release)** — `8bf7128` (feat)
2. **Task 2: Add lease lifecycle tests + tz/evaluator fix** — `21924f0` (test)

## Files Created/Modified
- `src/sqlery/fastapi_sqlery/backend.py` — Real `claim_queue_leases` / `_claim_one_lease` / `renew_queue_leases` / `release_queue_leases`; module-level `DaemonLease` + `IntegrityError` imports.
- `tests/unit/test_sqlalchemy_backend_sync.py` — `TestSQLAlchemyLeaseLifecycle` (10 SQLite tests) plus `_read_lease` / `_count_leases` helpers.

## Decisions Made
- **synchronize_session=False on the expired-take-over CAS:** The SQLite `expires_at` column stores naive datetimes; SQLAlchemy's ORM `update().where(expires_at < now)` evaluator compared the naive in-session value to the aware `now` and raised `TypeError`. Setting `synchronize_session=False` emits raw SQL (string-comparable timestamps in SQLite) and keeps the plan-specified `expires_at < now` guard.
- **UTC-aware normalization before Python comparisons:** `existing.expires_at` read back from SQLite is naive, so it is normalized (`dt if dt.tzinfo else dt.replace(tzinfo=UTC)`) before the `< now` / live-vs-expired branch — reusing the dt-normalization idiom already present in `backend.py`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SQLite naive/aware datetime comparison in `_claim_one_lease`**
- **Found during:** Task 2 (running the new lease lifecycle tests)
- **Issue:** SQLite returns `expires_at` as offset-naive; comparing it to `now = datetime.now(UTC)` (offset-aware) raised `TypeError: can't compare offset-naive and offset-aware datetimes` — both in the Python `if existing.expires_at < now` branch and inside the ORM `update().where(expires_at < now)` evaluator. This is a real correctness bug that would also break production SQLite lease take-over, not just the tests.
- **Fix:** (a) Normalize the read-back `expires_at` to UTC-aware before the Python comparison (matching the existing idiom in `backend.py`); (b) execute the expired-take-over CAS with `.execution_options(synchronize_session=False)` so SQLAlchemy emits raw SQL instead of running the ORM evaluator on the naive column.
- **Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
- **Commit:** `21924f0`

## Known Stubs

None — all three methods are fully wired to real `DaemonLease` rows; no placeholders, empty returns, or mock data sources remain.

## Issues Encountered

1. **Worktree venv lacked standalone/test deps.** As in Wave 1, the shared project `.venv` (Python 3.12) was missing `sqlmodel`/`pytest`. Resolved by reinstalling the project's *already-declared* extras (`uv pip install -e ".[standalone]"` then `.[dev]`) — no new dependency introduced (CLAUDE.md "no new dependencies"). Tests were run with the worktree `src` on `PYTHONPATH` so they exercise worktree source (confirmed via `backend.__file__`).
2. **Pre-existing F401 in the test file (out of scope).** `ruff` flags an unused `from sqlery.core.models import QueuedJob` at line 380, inside a pre-existing `TestWorkerLifecycle` test (owned by an earlier plan), far above the 161 lines this plan added. Left untouched per the scope boundary.
3. **Pre-existing E711/E712 in backend.py (out of scope).** `ruff` flags `== None` / `== True` SQLAlchemy ORM comparison patterns in pre-existing methods; none are in the lines this plan added. `black` is clean on both files.

## Verification Results
- Task 1 verify: `OVERRIDE + SIG OK` — all three methods override the ABC defaults and `claim_queue_leases` has the exact `[self, queues, daemon_id, node_id, pid, lease_secs]` signature.
- Task 2 verify: `pytest -k "Lease or lease or daemon_call_contract"` → **12 passed, 2 skipped** (the 2 skips are the Postgres mirror, auto-skipped without `SQLERY_TEST_PG_URL`).
- Full `test_sqlalchemy_backend_sync.py` → **84 passed, 6 skipped, 2 xfailed** (no regressions).
- `tests/chaos/test_lease_zombie.py` → **10 passed, 1 skipped** (lease tests now activate against the standalone backend).
- `src/sqlery/core/daemon.py` unchanged (diff against the plan base shows only `backend.py` and the test file).

## TDD Gate Compliance
Both tasks are `tdd="true"`. The plan structures Task 1 as the implementation (verified by an override/signature assertion) and Task 2 as the behavioral lease tests; the SQLite lease suite (`TestSQLAlchemyLeaseLifecycle`) is the GREEN proof for the implemented methods and surfaced the tz bug that was then fixed. A standalone RED `test(...)` commit preceding GREEN was not produced because the plan defined implementation-first/tests-second tasks; the behavioral suite nonetheless fully exercises the new methods and the daemon call contract.

## Self-Check: PASSED

(see appended self-check below)

---
*Phase: 08-standalone-lease-parity*
*Completed: 2026-06-08*
