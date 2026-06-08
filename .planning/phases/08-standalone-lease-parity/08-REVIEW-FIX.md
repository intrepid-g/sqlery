---
phase: 08-standalone-lease-parity
fixed_at: 2026-06-08T00:00:00Z
review_path: .planning/phases/08-standalone-lease-parity/08-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 08: Code Review Fix Report

**Fixed at:** 2026-06-08
**Source review:** .planning/phases/08-standalone-lease-parity/08-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (1 Critical + 6 Warnings)
- Fixed: 7
- Skipped: 0

## Fixed Issues

### CR-01: `SELECT FOR UPDATE SKIP LOCKED` on the lease row makes a live, locked lease look free and routes into INSERT

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`, `tests/unit/test_sqlalchemy_backend_sync.py`
**Commit:** 4b65121
**Applied fix:** Replaced `with_for_update(skip_locked=True)` with a blocking
`with_for_update()` on the single-key lease row in `_claim_one_lease`'s
`skip_locked` (Postgres) branch. SKIP LOCKED is the wrong primitive for a
single-keyed lease row — a locked-but-live row read back as `None` and routed
into the INSERT branch, leaving the take-over/refresh path unreachable under
contention. With a blocking lock, a concurrent claimant waits for the lock and
then observes the real row, matching the Django reference's contention
semantics. Added a regression test `test_expired_lease_taken_over_under_concurrent_lock`
to `TestLeaseLifecyclePostgres` that holds an EXPIRED lease row locked inside an
open transaction in one thread while a second daemon claims in another thread,
asserting the second daemon takes over the expired lease (the old SKIP LOCKED
path would have failed the INSERT and left it unclaimed).

**Requires human verification:** This is a concurrency/logic change. The new
regression test exercises the contended path but only runs against a real
Postgres service (`SQLERY_TEST_PG_URL`); it is skipped in the local SQLite-only
run. Confirm the two-transaction take-over behaves correctly against the CI
Postgres 15 service before proceeding.

### WR-01: Own-live-lease re-claim returns `True` here but Django returns `False` — parity divergence

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 30be67b
**Applied fix:** Documented the chosen contract in the `claim_queue_leases`
docstring rather than forcing a behavior change. The standalone backend
intentionally treats own-live re-claim as an idempotent refresh returning
`True` (asserted by the existing, passing `test_reclaim_own_live_lease_is_idempotent`),
while Django returns `False`. The divergence is currently latent because the
daemon caller only ever claims `queues - owned_queues`. Forcing standalone to
return `False` would break the committed/tested standalone contract and require
an out-of-scope Django change; the parity note records the intentional
divergence and warns callers not to assume cross-backend parity on this return
value. Also corrected the now-stale "PostgreSQL uses SELECT FOR UPDATE SKIP
LOCKED" line in the same docstring to reflect the CR-01 blocking-lock change.

### WR-02: `renew_queue_leases([], ...)` issues an unfiltered-by-queue UPDATE

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 73c7b92
**Applied fix:** Added `if not owned_queues: return` early-returns to both
`renew_queue_leases` and `release_queue_leases`, defending against the
`in_([])` dialect-dependent behavior / SQLAlchemy warning for direct callers.

### WR-03: SKIP LOCKED take-over is read-then-write, not a single conditional UPDATE (TOCTOU window)

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 4b65121
**Applied fix:** Resolved together with CR-01. The take-over in the Postgres
branch is now guarded by the same predicate as the version-CAS branch
(`expires_at < now OR own daemon_id`), and the blocking row lock from CR-01
serializes concurrent claimants so the read-then-write of the locked row is
contention-safe. Added an explanatory comment marking the shared predicate.

### WR-04: Naive/aware datetime mismatch is patched at read sites but the schema stores naive timestamps

**Files modified:** `src/sqlery/core/models.py`, `alembic/versions/20260608_0015_add_daemon_lease.py`
**Commit:** 4a1996f
**Applied fix:** Changed `acquired_at` / `expires_at` to timezone-aware columns:
`sa.DateTime(timezone=True)` in the migration and
`Field(sa_column=Column(DateTime(timezone=True), ...))` on the SQLModel. Added
`DateTime` to the `sqlalchemy` import in `models.py`. Postgres now stores
`timestamptz` and returns aware values; SQLite still returns naive, so the
existing read-site normalization is retained (per project convention, the old
naive column declarations are commented out, not deleted).

### WR-05: `version` column added to standalone `DaemonLease` but absent from Django model — schema divergence

**Files modified:** `src/sqlery/core/models.py`, `src/sqlery/django_sqlery/models.py`
**Commit:** 14acad3
**Applied fix:** Documented the intentional divergence in both model docstrings.
The standalone SQLModel carries a `version` column for SQLite optimistic-CAS
take-over; Django uses `SELECT FOR UPDATE SKIP LOCKED` and intentionally omits
it. Adding an unused `version` field to the Django model would require an
out-of-scope Django migration and the column would never be used; documenting
the divergence in both stacks (and the constraint that a single DB must not be
migrated by both) is the scope-respecting resolution.

### WR-06: `update_worker_heartbeat` create-branch ignores `jobs_processed`

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 9f3366b
**Applied fix:** Pass `jobs_processed=jobs_processed if jobs_processed is not None else 0`
in the `Worker(...)` create branch so a first heartbeat carrying a non-zero
count is no longer silently dropped (the update branch already handled it).

## Skipped Issues

None — all in-scope findings were fixed. (Info findings IN-01 through IN-04 are
out of scope under `fix_scope: critical_warning`.)

---

_Fixed: 2026-06-08_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
