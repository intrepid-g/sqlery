---
phase: 08-standalone-lease-parity
reviewed: 2026-06-08T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/core/models.py
  - src/sqlery/tables.py
  - alembic/env.py
  - alembic/versions/20260608_0015_add_daemon_lease.py
  - src/sqlery/fastapi_sqlery/backend.py
  - tests/unit/test_sqlalchemy_backend_sync.py
findings:
  critical: 0
  warning: 1
  info: 3
  total: 4
status: issues_found
---

# Phase 08: Code Review Report

**Reviewed:** 2026-06-08
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Iteration 2 re-review of the standalone per-queue lease parity work (`DaemonLease`
SQLModel, Alembic migration `20260608_0015`, and `claim_queue_leases` /
`renew_queue_leases` / `release_queue_leases` on `SQLAlchemyBackend`). Focus was
confirming the prior 1 BLOCKER + 6 warnings are genuinely fixed (not merely
masked) and surfacing any regressions introduced by the fixes.

**Prior findings — all confirmed resolved:**

- **CR-01 (BLOCKER)** — RESOLVED. The Postgres lease probe now uses a blocking
  `with_for_update()` (no `skip_locked`); the old SKIP-LOCKED variant is
  commented out per house rules (`backend.py:303-321`). The take-over branch
  (lines 342-365) is now reachable under contention because a concurrent
  claimant blocks on the row lock and then observes the real row. A dedicated
  PG regression test was added (`test_expired_lease_taken_over_under_concurrent_lock`,
  test lines 979-1054) that holds a lock on an expired row in one transaction
  while a second daemon takes it over — exactly the concurrent-lock scenario the
  prior tests could not exercise.
- **WR-01** — RESOLVED (documented divergence). The own-live re-claim returning
  `True` vs Django's `False` is now spelled out in a multi-line docstring with
  the latency rationale (`backend.py:243-252`).
- **WR-02** — RESOLVED. Both `renew_queue_leases` (lines 463-464) and
  `release_queue_leases` (lines 491-492) early-return on empty `owned_queues`.
- **WR-03** — RESOLVED. The SKIP-LOCKED-branch take-over is now guarded by the
  same `expired OR own-lease` predicate as the version-CAS branch and is
  serialized by the blocking row lock (`backend.py:349-362`).
- **WR-04** — RESOLVED. Columns are now `DateTime(timezone=True)` in both the
  model (`models.py:334-335`) and the migration (`20260608_0015:36-37`); old
  naive columns commented out.
- **WR-05** — RESOLVED. The `version`-column schema divergence is now documented
  in both the standalone model (`models.py:310-320`) and the Django model
  (`django_sqlery/models.py:1199-1204`).
- **WR-06** — RESOLVED. The `update_worker_heartbeat` create branch now passes
  `jobs_processed` (`backend.py:647-650`).

The full SQLite suite passes (84 passed, 7 PG-skipped, 2 documented xfail). No
new BLOCKER was introduced. One genuine WARNING-level inconsistency between the
two SQLite-branch take-over UPDATEs remains, plus three INFO items. No SQL
injection surface (all queries parameterized via ORM); no secrets; no dangerous
calls. The migration revision chain is intact (0015 → 0014 → 0013).

## Warnings

### WR-01: SQLite own-live re-claim CAS lacks `synchronize_session=False`, unlike its sibling expired-takeover CAS

**File:** `src/sqlery/fastapi_sqlery/backend.py:399-415` (vs `419-439`)
**Issue:** Within the version-CAS (`optimistic_version` / `basic_lock`) branch
there are two `update(DaemonLease)` statements that are meant to be siblings:

- The **own-live re-claim** UPDATE (lines 400-413) filters on
  `queue_name == ... AND version == current_version` and does **not** set
  `.execution_options(synchronize_session=False)`.
- The **expired take-over** UPDATE (lines 420-436) filters on
  `... AND version == ... AND expires_at < now` and **does** set
  `synchronize_session=False`, with an explicit comment that the ORM
  synchronize evaluator cannot compare the SQLite naive `expires_at` column
  against the aware `now`.

The two are asymmetric without a stated reason. The own-live branch happens to
avoid the evaluator's datetime problem only because its `WHERE` clause has no
datetime predicate — but `existing` (the ORM object read at line 368) is still
attached to the same session, so the default `synchronize_session='evaluate'`
will attempt to evaluate the `version == current_version` predicate against the
in-memory object and synchronize its attributes. This works today, but it is a
latent fragility: any future addition of a datetime/JSON predicate to that
UPDATE (e.g. tightening own-live re-claim to also require non-expiry, mirroring a
future Django change) would silently raise the same evaluator `TypeError` the
expired branch was patched to avoid. Two CAS statements that operate on the same
table in the same method should share one consistent rule, not two.

**Fix:** Add the same option to the own-live re-claim UPDATE so both lease CAS
statements behave identically and are future-proof:

```python
cas_stmt = (
    update(DaemonLease)
    .where(DaemonLease.queue_name == queue_name)
    .where(DaemonLease.version == current_version)
    .values(
        daemon_id=daemon_id,
        node_id=node_id,
        pid=pid,
        acquired_at=now,
        expires_at=expires,
        version=current_version + 1,
    )
    .execution_options(synchronize_session=False)  # match the expired-takeover CAS
)
```

## Info

### IN-01: Duplicated naive→aware normalization ternary still appears at 4+ sites

**File:** `src/sqlery/fastapi_sqlery/backend.py:344-348, 392-396` and
`src/sqlery/core/models.py:167, 187`
**Issue:** The prior IN-04 noted the `dt if dt.tzinfo else dt.replace(tzinfo=UTC)`
idiom is copy-pasted. The WR-04 fix moved the lease columns to tz-aware (so
Postgres reads come back aware), but the read-site ternary is still required for
SQLite (which still returns naive) and remains inlined twice inside
`_claim_one_lease` (lines 344-348 and 392-396) plus twice in `models.py`. The
schema change reduced the blast radius but did not eliminate the duplication.

**Fix:** Promote a module-level `_aware(dt)` helper in `backend.py` and call it
at all sites, including both lease branches and `get_running_jobs_for_liveness`.

### IN-02: `determine_claim_strategy` `basic_lock` remains a named-but-unimplemented third strategy

**File:** `src/sqlery/fastapi_sqlery/backend.py:21-39`, consumed at line 303 and 367-442
**Issue:** `basic_lock` (returned for MySQL/Oracle/`None`) has no distinct branch
anywhere; both `claim_job` and `_claim_one_lease` treat anything that is not
`skip_locked` as the version-CAS (`else`) path. The "I wish I had the time to"
comment (line 32) confirms it is aspirational. Tests
(`test_mysql_uses_basic_lock`, `test_none_falls_back_to_basic_lock`) pin the
string value but not any behavior. This is correctness-neutral (MySQL silently
gets the version-CAS path) but the third name is misleading.

**Fix:** Either collapse to two strategies (`skip_locked` / `optimistic_version`)
or implement a real blocking `SELECT ... FOR UPDATE` branch for `basic_lock`.

### IN-03: `claim_queue_leases` commits per queue; a mid-loop exception leaves earlier queues claimed but discards the partial result

**File:** `src/sqlery/fastapi_sqlery/backend.py:264-274`
**Issue:** Each `_claim_one_lease` commits independently within the shared
session. If queue N raises after queues `0..N-1` committed, the exception
propagates and the `claimed` list is never returned, so the daemon believes it
owns nothing while the DB rows say otherwise. This matches Django's per-queue
commit model (`django_sqlery/backend.py:912-916`), and leases self-heal via TTL
expiry within `lease_secs`, so it is acceptable for parity — noted for
completeness only.

**Fix:** Acceptable as-is given TTL recovery. Optionally wrap the per-queue claim
in try/except and return the partial `claimed` list rather than propagating.

---

_Reviewed: 2026-06-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
