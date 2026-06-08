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
  critical: 1
  warning: 6
  info: 4
  total: 11
status: issues_found
---

# Phase 08: Code Review Report

**Reviewed:** 2026-06-08
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 08 adds a standalone per-queue lease (`DaemonLease` SQLModel, Alembic migration `20260608_0015`, and `claim_queue_leases` / `renew_queue_leases` / `release_queue_leases` on `SQLAlchemyBackend`) mirroring the Django implementation. The review focused on atomicity of the Postgres `SELECT FOR UPDATE SKIP LOCKED` vs SQLite version-CAS paths, timezone-aware datetime comparison, take-over races, SQL injection, and Django parity.

Overall the ORM-based queries are parameterized (no SQL injection surface), the SQLite version-CAS take-over is well guarded, and timezone normalization is handled at the comparison sites. However, there is one **BLOCKER**: the Postgres `SELECT FOR UPDATE SKIP LOCKED` lease-claim path is not actually atomic against a concurrently-locked live row — `skip_locked=True` causes a locked row to read back as `None`, which then routes into the INSERT branch and corrupts the take-over semantics. There are also several parity divergences from the Django reference (own-live-lease re-claim returns `True` here but `False` in Django; the SKIP LOCKED expired take-over is read-then-write rather than a single conditional UPDATE) and a `renew_queue_leases` no-op-on-empty-list footgun.

## Critical Issues

### CR-01: `SELECT FOR UPDATE SKIP LOCKED` on the lease row makes a live, locked lease look free and routes into INSERT

**File:** `src/sqlery/fastapi_sqlery/backend.py:291-336`
**Issue:**
In the `skip_locked` branch of `_claim_one_lease`, the existing-row probe is:

```python
stmt = (
    select(DaemonLease)
    .where(DaemonLease.queue_name == queue_name)
    .with_for_update(skip_locked=True)
)
existing = session.exec(stmt).first()
if existing is None:
    lease = DaemonLease(...)
    session.add(lease)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True
```

`SKIP LOCKED` instructs Postgres to *omit* rows currently locked by another transaction rather than block on them. So when daemon B probes a queue whose lease row is held (locked) inside daemon A's still-open transaction, the `SELECT ... FOR UPDATE SKIP LOCKED` returns **zero rows** — `existing is None` is `True` even though a live row exists. Daemon B then falls into the INSERT branch.

This is masked only because `queue_name` is the primary key, so the duplicate INSERT raises `IntegrityError` and returns `False`. That happens to be the safe outcome here, but it is accidental, not designed:

1. The take-over / idempotent-refresh logic (lines 317-336) is **unreachable whenever another transaction holds the row lock** — the only time take-over contention actually matters. The expired-lease take-over therefore silently degrades: under contention the loser does not take over the expired lease, it just fails the INSERT and returns `False`, so an *expired* lease that is momentarily locked by another (also-failing) claimant is left unclaimed for that cycle.
2. The pattern is the wrong primitive. The Django reference (`django_sqlery/backend.py:918-961`) does a single **conditional `UPDATE ... WHERE expires_at < now`** inside `@transaction.atomic`, then an INSERT — it never relies on `SKIP LOCKED` for the lease and never has a "locked row reads as absent" hole. `SKIP LOCKED` is correct for *job claiming* (you want to skip a locked job and grab a different one) but wrong for a *single-keyed lease row* where there is no "different row" to fall through to.

**Fix:** Do not use `with_for_update(skip_locked=True)` for the lease row. Either mirror Django's conditional-UPDATE-then-INSERT for Postgres, or use a blocking `with_for_update()` (no `skip_locked`) so a concurrent claimant waits for the lock and then sees the real row:

```python
# Postgres: blocking row lock so the real row is always observed
stmt = (
    select(DaemonLease)
    .where(DaemonLease.queue_name == queue_name)
    .with_for_update()          # do NOT skip_locked on a single-key lease row
)
existing = session.exec(stmt).first()
# ... existing take-over / insert logic now reachable under contention
```

Alternatively, prefer the Django-parity conditional UPDATE which is take-over-atomic in one statement:

```python
res = session.exec(
    update(DaemonLease)
    .where(DaemonLease.queue_name == queue_name)
    .where(DaemonLease.expires_at < now)
    .values(daemon_id=daemon_id, node_id=node_id, pid=pid,
            acquired_at=now, expires_at=expires)
    .execution_options(synchronize_session=False)
)
if res.rowcount == 1:
    session.commit()
    return True
# then attempt INSERT, catch IntegrityError -> live lease held elsewhere
```

Note: the existing PG tests (`TestLeaseLifecyclePostgres`) only exercise the *uncontended* sequential path (claim, then a second daemon claims after the first commit), so they never lock a row across the probe and cannot catch this. A test with two concurrent open transactions is required to expose it.

## Warnings

### WR-01: Own-live-lease re-claim returns `True` here but Django returns `False` — parity divergence

**File:** `src/sqlery/fastapi_sqlery/backend.py:324, 370-386`
**Issue:** Both lease-claim branches treat `existing.daemon_id == daemon_id` as a successful (re)claim and refresh `expires_at`, returning `True` (SKIP LOCKED branch line 324 `or existing.daemon_id == daemon_id`; version-CAS branch lines 370-386). The Django reference (`django_sqlery/backend.py:935-961`) only updates rows with `expires_at < now`; a daemon re-claiming its **own still-live** lease matches neither the expired-UPDATE nor the INSERT (PK conflict → `IntegrityError` → `False`). So the two backends disagree on the return value for "re-claim my own live lease."

In practice the daemon caller (`core/daemon.py:362-418`) only ever calls `claim_queue_leases` for `queues - owned_queues`, so it never re-claims a queue it already owns, and the divergence is currently latent. But this is exactly the kind of cross-backend semantic drift the phase set out to eliminate, and any future caller (or test) that relies on Django semantics will break on standalone.

**Fix:** Pick one contract and document it. Either make Django return `True` for own-live re-claim, or make the standalone version return `False` for `existing.daemon_id == daemon_id and existing_expires >= now` to match Django. Add a parity note in the docstring stating the chosen semantics.

### WR-02: `renew_queue_leases([], ...)` issues an unfiltered-by-queue UPDATE

**File:** `src/sqlery/fastapi_sqlery/backend.py:432-440`
**Issue:** When `owned_queues` is empty, `DaemonLease.queue_name.in_([])` is a valid (always-false) predicate, so the UPDATE matches nothing — that part is fine. But the daemon guards this call with `if owned_queues:` (`core/daemon.py:406-407`), so the empty case is never reached there. The risk is that the method itself does not defend against the empty list, and `in_([])` emits a SQLAlchemy warning on some versions and can behave inconsistently across dialects. `release_queue_leases` (lines 456-463) has the same shape. This is defensive-hardening rather than a live bug, but a direct caller passing `[]` relies entirely on dialect behavior of `IN ()`.

**Fix:** Early-return on empty input in both `renew_queue_leases` and `release_queue_leases`:

```python
if not owned_queues:
    return
```

### WR-03: SKIP LOCKED take-over is read-then-write, not a single conditional UPDATE (TOCTOU window)

**File:** `src/sqlery/fastapi_sqlery/backend.py:317-333`
**Issue:** In the SKIP LOCKED branch, take-over of an expired lease reads `existing`, evaluates `existing_expires < now` in Python, then mutates and commits the same ORM object. The `FOR UPDATE` row lock does protect this once the row is actually returned — but combined with CR-01 (locked rows read as `None`) and the fact that the version-CAS branch *does* guard the UPDATE with `WHERE expires_at < now AND version == current_version` (lines 390-407), the SKIP LOCKED branch is inconsistent with its own SQLite sibling. The SQLite path is the more defensive of the two; the Postgres path leans entirely on the row lock that CR-01 shows can be bypassed.

**Fix:** After resolving CR-01 (blocking lock or conditional UPDATE), also add the `expires_at < now` / `daemon_id` guard to the UPDATE in the Postgres branch so the two backends share identical take-over predicates.

### WR-04: Naive/aware datetime mismatch is patched at read sites but the schema stores naive timestamps — fragile and duplicated

**File:** `src/sqlery/core/models.py:319, 320` and `alembic/versions/20260608_0015_add_daemon_lease.py:29-30`
**Issue:** The migration declares `acquired_at` / `expires_at` as `sa.DateTime()` (TIMESTAMP WITHOUT TIME ZONE). The model annotates them as `datetime` (no tz) and writes `datetime.now(UTC)` (aware) values into them. Every comparison site then has to re-normalize naive reads back to UTC (`backend.py:319-323`, `362-367`, and the same pattern repeated in `models.py:167`, `187`, `get_running_jobs_for_liveness:1009-1012`). This "store aware, read naive, re-attach UTC everywhere" pattern is the root cause of the pre-existing `get_expired_ttl_jobs` `TypeError` bug the tests mark `xfail` (`test_sqlalchemy_backend_sync.py:253-280`). Adding `DaemonLease` perpetuates it.

**Fix:** Use timezone-aware columns so reads come back aware and no re-normalization is needed: `sa.DateTime(timezone=True)` in the migration and `Field(sa_column=Column(DateTime(timezone=True)))` on `acquired_at` / `expires_at`. (Postgres stores `timestamptz`; SQLite still returns naive, so keep the normalization helper but centralize it rather than inlining the ternary at each call site.)

### WR-05: `version` column added to standalone `DaemonLease` but absent from Django model — schema divergence

**File:** `src/sqlery/core/models.py:321-322` vs `src/sqlery/django_sqlery/models.py:1191-1206`
**Issue:** The standalone `DaemonLease` carries a `version` field for SQLite CAS; the Django `DaemonLease` has no such column. The two "mirrored" tables (`sqlery_daemon_lease`) now have different schemas depending on which backend created them. If a single deployment ever runs migrations from both stacks against the same database (or a tool inspects the table cross-backend), the column set will not match. The migration comment acknowledges this ("plus a version column for SQLite CAS") but the divergence is not enforced or documented in the Django model.

**Fix:** Either add a matching `version = models.IntegerField(default=0)` to the Django `DaemonLease` (so both stacks produce identical DDL), or explicitly document in both models that the standalone table intentionally carries an extra `version` column that Django ignores.

### WR-06: `update_worker_heartbeat` create-branch ignores `jobs_processed`

**File:** `src/sqlery/fastapi_sqlery/backend.py:602-610`
**Issue:** Not in the lease scope but in a reviewed file: when the worker row does not yet exist, the `else` branch constructs `Worker(...)` without passing `jobs_processed`, so a first heartbeat that supplies `jobs_processed` silently drops it (defaults to 0). The update branch (lines 600-601) handles it correctly. This is an inconsistency that will under-report stats for a worker whose very first heartbeat carries a non-zero count.

**Fix:** Pass it through in the create branch:

```python
worker = Worker(
    id=worker_id,
    ...
    jobs_processed=jobs_processed if jobs_processed is not None else 0,
)
```

## Info

### IN-01: `claim_queue_leases` commits per-queue inside a shared session, so a partial failure leaves some leases claimed

**File:** `src/sqlery/fastapi_sqlery/backend.py:252-262`
**Issue:** The loop calls `_claim_one_lease` per queue, and each call commits independently within the same session. If queue N raises mid-loop, queues `0..N-1` are already committed/claimed but the returned `claimed` list is never returned (exception propagates), so the daemon believes it owns nothing while the DB says otherwise. This matches Django's per-queue commit model, so it is acceptable for parity, but worth noting: leases are self-healing via TTL expiry, so the orphaned rows recover within `lease_secs`.

**Fix:** Acceptable as-is given TTL recovery; optionally wrap the per-queue claim in try/except and return the partial `claimed` list rather than propagating.

### IN-02: `determine_claim_strategy` `basic_lock` fallback has no distinct behavior from `optimistic_version`

**File:** `src/sqlery/fastapi_sqlery/backend.py:21-39, 338-413`
**Issue:** `basic_lock` (MySQL/Oracle/etc.) is returned by the strategy function but every consumer treats anything that is not `skip_locked` identically to `optimistic_version` (the `else` path). The named third strategy is effectively dead — there is no `basic_lock`-specific branch. The "I wish I had the time to" comment (line 32) confirms it is aspirational.

**Fix:** Either collapse to two strategies (`skip_locked` / `optimistic_version`) or implement a real `SELECT ... FOR UPDATE` (blocking) branch for `basic_lock`. As-is, MySQL would silently rely on the version-CAS path, which is fine for correctness but the third name is misleading.

### IN-03: `version` not exposed via the standalone model's optimistic-lock semantics for renew/release

**File:** `src/sqlery/fastapi_sqlery/backend.py:432-463`
**Issue:** `renew_queue_leases` and `release_queue_leases` filter on `daemon_id` but do not touch `version`, while the claim path increments it. The version counter is therefore only meaningful for the claim race, not renew/release. This is fine (renew/release are already `daemon_id`-scoped), but the asymmetry is undocumented and a future reader may assume `version` guards all three operations.

**Fix:** Add a one-line docstring note that `version` guards only the claim/take-over CAS, not renew/release (which are owner-scoped by `daemon_id`).

### IN-04: Duplicated naive→aware normalization ternary appears 4+ times

**File:** `src/sqlery/fastapi_sqlery/backend.py:319-323, 363-367, 1009-1012` and `src/sqlery/core/models.py:167, 187`
**Issue:** The `dt if dt.tzinfo else dt.replace(tzinfo=UTC)` idiom is copy-pasted across the module. `get_running_jobs_for_liveness` already factored it into a local `_aware` helper (line 1009). The lease methods re-inline it.

**Fix:** Promote `_aware(dt)` to a module-level helper in `backend.py` (or a shared util) and call it from all sites, including the two lease branches.

---

_Reviewed: 2026-06-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
