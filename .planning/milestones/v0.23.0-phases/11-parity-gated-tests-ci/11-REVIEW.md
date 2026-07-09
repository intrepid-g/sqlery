---
phase: 11-parity-gated-tests-ci
reviewed: 2026-06-08T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - src/sqlery/compat/__init__.py
  - .github/workflows/test.yml
  - tests/test_parity_scheduler.py
  - tests/test_atomic_scheduler.py
  - tests/test_core_standalone.py
  - tests/chaos/test_lease_zombie.py
  - tests/settings.py
  - pyproject.toml
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 11: Code Review Report

**Reviewed:** 2026-06-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Iteration-2 re-review of the parity-gated-tests CI work. The prior BLOCKER (CR-01)
and all four warnings were verified against the source and cross-referenced with the
worker, backend, and conftest helpers they depend on.

**CR-01 is genuinely resolved.** The `standalone_pg` marker is registered in
`pyproject.toml` (lines 147-152) and applied to exactly the three genuinely-standalone
locations: `TestStandaloneAdvanceScheduledTaskPostgres` (test_core_standalone.py:326),
`TestStandaloneLeaseFailoverPostgres` (test_lease_zombie.py:414), and
`test_bare_worker_standalone_real_process` (test_parity_scheduler.py:210). The CI
"Run standalone-mode parity suite with PostgreSQL" step now selects
`-m "postgres and standalone_pg"` (test.yml:136-143). I traced marker resolution:
the Django-mode PG cells that previously errored under a forced-uninitialized standalone
backend — `TestParityFailover.test_failover_postgres_real_backend` (carries
`@pytest.mark.django_db`, NOT `standalone_pg`) and `TestCronSemanticsHardeningPostgres`
(carries `postgres` only, NOT `standalone_pg`) — are now correctly excluded by the
conjunction selector. The selector collects exactly 4 cells (2 from
`TestStandaloneAdvanceScheduledTaskPostgres`, 1 from `TestStandaloneLeaseFailoverPostgres`,
1 from the `[postgres]` param of the bare-worker cell), none of which carry `django_db`,
so pytest-django never attempts DB setup for them under `SQLERY_FORCE_STANDALONE=1`. The
`--collect-only` precheck will therefore see >0 cells and the empty-collection gate is meaningful.

**WR-01 verified.** `_lease_supported` now catches `RuntimeError` before the blanket
`except Exception: return True` in both `test_parity_scheduler.py:91` and
`test_lease_zombie.py:249`. I confirmed `SQLAlchemyBackend.claim_queue_leases` opens
`self._get_session()` (backend.py:264) BEFORE iterating the queue list, so even the
probe's `queues=[]` call raises `RuntimeError("Database not initialized")`
(database.py:100-101) on an uninitialized engine — the probe now degrades to a clean skip
instead of falsely reporting support.

**WR-02 verified, with a scope caveat (WR-A below).** `tests/settings.py` switches Django's
test DB to Postgres when `SQLERY_TEST_PG_URL`/`DATABASE_URL` is a postgres DSN and falls back
to in-memory SQLite otherwise. The default (no env) path correctly resolves to
`:memory:` SQLite, so default rails are not broken. `_is_postgres_url` correctly handles the
`postgresql+driver://` form via `startswith("postgresql+")`.

**WR-03 verified.** The precheck (`--collect-only`) uses the same
`-m "postgres and standalone_pg"` selection across the same three paths, so it asserts the
standalone cells specifically.

**WR-04 verified.** `TestLeaseExpiry.test_expired_lease_can_be_taken_over` now forces expiry
via a PAST `expires_at` `.update()` (test_lease_zombie.py:292-294) instead of
`time.sleep(1.5)`; the old sleep is preserved as a commented `# Old:` block per project
convention.

The bare-worker subprocess script's `worker_module.close_old_connections = None` override
is safe: `WorkerProcess.run` guards `if close_old_connections is not None` (worker.py:537),
so a no-op-by-None will not crash the standalone worker. `_run_no_django` scrubs
`DJANGO_SETTINGS_MODULE` and forces `SQLERY_FORCE_STANDALONE=1`, matching the conftest convention.

No BLOCKER-tier defects remain. Two WARNINGs and two INFO items below.

## Warnings

### WR-A: WR-02 silently widens the "Run tests with PostgreSQL" step beyond parity cells

**File:** `tests/settings.py:20-45`, `.github/workflows/test.yml:69-74`
**Issue:** `skip_on_sqlite` in `tests/test_atomic_scheduler.py:36-39` is evaluated at
collection time from `connection.vendor`, which is derived from `settings.DATABASES`. Before
WR-02, the "Run tests with PostgreSQL" step (which sets `DATABASE_URL=postgres...` but NOT
`SQLERY_TEST_PG_URL`) still resolved in-memory SQLite, so the `@skip_on_sqlite` concurrency
tests (`test_concurrent_schedulers_no_duplicate_enqueueing`,
`test_skip_locked_prevents_scheduler_blocking`, the whole `TestAtomicSchedulerPerformance`
class) were skipped. After WR-02, `tests/settings.py` now resolves Postgres from
`DATABASE_URL`, so `connection.vendor == "postgresql"` and these threaded, lock-contention,
timing-sensitive tests now actually EXECUTE in that step — a behavioral expansion not
described in the WR-02 fix scope. These tests assert wall-clock bounds
(`assert elapsed < 0.2`, `time.sleep(0.5)` hold windows) and rely on real concurrent
connections; under a shared CI Postgres service they are plausible flake sources. This is
arguably an improvement (more coverage), but it is an unintended side effect of a settings
change and should be a conscious decision, not a silent consequence.
**Fix:** Either (a) document in the step/settings comment that WR-02 intentionally promotes
the SQLite-skipped concurrency tests to real PG execution here, or (b) if the intent was to
keep that step's scope unchanged, gate the Django Postgres switch on `SQLERY_TEST_PG_URL`
only (not `DATABASE_URL`) so the legacy "Run tests with PostgreSQL" step keeps its prior
SQLite collection semantics:
```python
# tests/settings.py — narrow the trigger to the PG-rail env var only
_PG_URL = os.environ.get("SQLERY_TEST_PG_URL")  # not ... or DATABASE_URL
```

### WR-B: `db` fixture's runtime `add_marker(postgres)` is dead w.r.t. `-m` selection

**File:** `tests/test_parity_scheduler.py:63-67`
**Issue:** Inside the `db` fixture, `request.node.add_marker(pytest.mark.postgres)` runs at
fixture-setup time, which is AFTER collection-time `-m` marker selection has already
happened. The only marker that actually drives `-m postgres` /
`-m "postgres and standalone_pg"` selection is the static
`pytest.param("postgres", marks=pytest.mark.postgres)` on line 58. The runtime `add_marker`
is therefore a no-op for selection and is misleading — a future maintainer could rely on it
for selection and silently mis-scope a cell. (Selection is correct today only because of the
static param mark.)
**Fix:** Remove the redundant runtime marker, or add an inline comment that it is decorative
only (used for `request.node.iter_markers` introspection, not for `-m` selection):
```python
if request.param == "postgres":
    if not os.environ.get("SQLERY_TEST_PG_URL"):
        pytest.skip("postgres engine requires SQLERY_TEST_PG_URL")
    # NOTE: -m selection is driven by the static param marks above; this
    # add_marker runs post-collection and does NOT affect -m filtering.
    request.node.add_marker(pytest.mark.postgres)
```

## Info

### IN-01: `prior_scheduled` assigned but unused in PG drift test

**File:** `tests/test_atomic_scheduler.py:720-721`
**Issue:** `prior_scheduled = task.next_run_at` is assigned and immediately copied into
`last_next` on line 721, but `prior_scheduled` itself is never referenced again (the same
dead binding exists in the SQLite twin at lines 501-502). Minor clutter; not a correctness
issue.
**Fix:** Drop the intermediate: `last_next = task.next_run_at`.

### IN-02: Unused `locked_task` bindings in legacy atomic-scheduler tests

**File:** `tests/test_atomic_scheduler.py:149`, `299`
**Issue:** `locked_task` is bound by `select_for_update(...).get(...)` but never read (the
test only needs the row lock as a side effect). These are pre-existing in the non-parity
legacy tests (not introduced by this phase) and harmless, but they are dead bindings flagged
for hygiene.
**Fix:** Drop the assignment (call `.get(...)` for its locking side effect) or prefix with
`_locked_task` to signal intentional discard. Low priority.

---

_Reviewed: 2026-06-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
