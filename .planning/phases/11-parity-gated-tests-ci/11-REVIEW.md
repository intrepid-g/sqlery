---
phase: 11-parity-gated-tests-ci
reviewed: 2026-06-08T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/compat/__init__.py
  - .github/workflows/test.yml
  - tests/test_parity_scheduler.py
  - tests/test_atomic_scheduler.py
  - tests/test_core_standalone.py
  - tests/chaos/test_lease_zombie.py
findings:
  critical: 1
  warning: 4
  info: 2
  total: 7
status: issues_found
---

# Phase 11: Code Review Report

**Reviewed:** 2026-06-08
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 11 adds {Django, standalone} × {SQLite, Postgres} parity behavioral tests and a CI gate.
The verification targets requested in the prompt mostly hold up:

- **`_detect_mode` override is safe.** `SQLERY_FORCE_STANDALONE=1` is set in exactly two
  standalone launch paths (`fastapi_sqlery/subprocess_executor.py:88`,
  `tests/integration/conftest.py:491`) plus the parity CI step and `test_lambda_standalone.py`.
  No Django launcher (`worker_runner.py`, `daemon_runner.py`, `worker_pool.py`) ever sets it, so a
  real Django deployment can never be wrongly forced standalone. Placing the check before the
  `"django" in sys.modules` branch does not regress Django detection (Django mode is only entered
  when the flag is absent AND settings are configured).
- **Test fidelity is good.** The new cells assert real behavior: the load-bearing assertion is a
  freshly-enqueued `QueuedJob` count (`== 1`), failover is simulated via PAST `expires_at`/`next_run_at`
  writes (no real TTL sleeps), the hardening cells drive the real `sqlery.core.scheduler.Scheduler` +
  `advance_scheduled_task_if_due` (NOT the legacy `executor.TaskExecutor`), standalone cells pin
  `SQLAlchemyBackend` via fixtures, and PG cells SKIP cleanly without `SQLERY_TEST_PG_URL`.

However, the new **"Run standalone-mode parity suite with PostgreSQL"** CI step has a
backend/ORM mode mismatch that will make the step ERROR (not pass, not clean-skip) on the CI
PostgreSQL rail. This is a build-breaking gate defect — see CR-01. There are also several
robustness/quality issues that mask or compound it (WR-01..WR-04).

## Critical Issues

### CR-01: Standalone parity CI step runs Django-ORM PG cells under a forced, uninitialized standalone backend — will ERROR on the PG rail

**File:** `.github/workflows/test.yml:101-115` (and the cells it selects: `tests/test_parity_scheduler.py:134-167`, `tests/test_atomic_scheduler.py:657-745`)

**Issue:**
The new step sets `SQLERY_FORCE_STANDALONE: "1"` and runs three files under `-m postgres`:

```
tests/test_parity_scheduler.py
tests/test_core_standalone.py
tests/test_atomic_scheduler.py
```

`SQLERY_FORCE_STANDALONE=1` forces `compat.get_backend()` to return `SQLAlchemyBackend`
(`src/sqlery/compat/__init__.py:855`). But two of those files contain **Django-mode** PG cells that
use the Django ORM directly and resolve `get_backend()` *without ever initializing the SQLAlchemy
engine*:

- `tests/test_parity_scheduler.py::TestParityFailover::test_failover_postgres_real_backend`
  (`@pytest.mark.postgres @pytest.mark.django_db`) calls `get_backend()` (forced →
  `SQLAlchemyBackend`) then `_claim(backend, ...)` → `backend.claim_queue_leases(...)`.
- `tests/test_atomic_scheduler.py::TestCronSemanticsHardeningPostgres.*` (`@pytest.mark.postgres
  @pytest.mark.django_db`) builds tasks via Django `ScheduledTask.objects.create(...)` then calls
  `scheduler.backend.advance_scheduled_task_if_due(...)` where `scheduler.backend = get_backend()`
  (forced → `SQLAlchemyBackend`).

`SQLAlchemyBackend._get_session = get_session` (`src/sqlery/fastapi_sqlery/backend.py:52`), and
`get_session()` → `get_engine()` raises `RuntimeError("Database not initialized...")` when the
module-global `_engine is None` (`src/sqlery/fastapi_sqlery/database.py:99-104`). In this CI step:

- `initialize()` / `init_database()` is never called.
- The only fixtures that set `_engine` are `standalone_backend` / `pg_standalone_backend` in
  `tests/test_core_standalone.py`, which monkeypatch `_engine` and **restore it to `None` on
  teardown**. These Django cells do not use those fixtures, and `test_parity_scheduler.py` runs
  *before* `test_core_standalone.py` in the listed order — so `_engine` is `None` when they run.
- The `tests/unit/` autouse `_patch_get_backend` fixture (`tests/unit/conftest.py:716`) and the
  `tests/integration/` `_reset_backend` fixtures are directory-scoped and do **not** apply to these
  top-level files.

On the CI PG rail `SQLERY_TEST_PG_URL` is set, so the in-body skip guards do NOT fire and the cells
execute. `advance_scheduled_task_if_due` / `claim_queue_leases` then hit `get_engine()` and raise
`RuntimeError`. Result: the step ERRORS for the wrong reason — it does not validate "standalone ×
Postgres" at all for these two files; it tests an uninitialized standalone backend driven by
Django-ORM-seeded rows that live in a *different* database (the Django in-memory SQLite test DB; see
WR-02). The only genuinely standalone-correct cells selected are
`test_core_standalone.py::TestStandaloneAdvanceScheduledTaskPostgres` /
`TestStandaloneLeaseFailoverPostgres` (own fixture-managed PG engine) and
`test_parity_scheduler.py::TestParityBareWorkerE2E::test_bare_worker_standalone_real_process[postgres]`
(real `_run_no_django` subprocess).

**Fix:** Scope the forced-standalone step to the cells that are actually standalone, or deselect the
Django-mode cells. Two viable approaches (commenting out the broad run rather than deleting it, per
project rules):

```yaml
    - name: Run standalone-mode parity suite with PostgreSQL
      env:
        PYTHONPATH: .
        SQLERY_FORCE_STANDALONE: "1"
        SQLERY_TEST_PG_URL: postgresql://postgres:postgres@localhost:5432/postgres
        DATABASE_URL: postgresql://postgres:postgres@localhost:5432/sqlery_test
      run: |
        # Old (selects Django-ORM cells that resolve an uninitialized SQLAlchemy
        # backend and ERROR — see CR-01):
        # uv run pytest -m postgres --co -q \
        #   tests/test_parity_scheduler.py tests/test_core_standalone.py tests/test_atomic_scheduler.py
        # uv run pytest -m postgres -v --tb=short \
        #   tests/test_parity_scheduler.py tests/test_core_standalone.py tests/test_atomic_scheduler.py
        # New: restrict to the standalone-backend cells only.
        uv run pytest -m postgres --co -q \
          tests/test_core_standalone.py::TestStandaloneAdvanceScheduledTaskPostgres \
          tests/test_core_standalone.py::TestStandaloneLeaseFailoverPostgres \
          "tests/test_parity_scheduler.py::TestParityBareWorkerE2E::test_bare_worker_standalone_real_process"
        uv run pytest -m postgres -v --tb=short \
          tests/test_core_standalone.py::TestStandaloneAdvanceScheduledTaskPostgres \
          tests/test_core_standalone.py::TestStandaloneLeaseFailoverPostgres \
          "tests/test_parity_scheduler.py::TestParityBareWorkerE2E::test_bare_worker_standalone_real_process"
```

Alternatively, give the standalone-running Django-ORM cells an `initialize()`-backed engine via a
shared fixture, but the cleaner fix is to stop forcing standalone mode over Django-mode tests. The
Django × Postgres cells are already covered by the unforced `Run @pytest.mark.postgres suite` step.

## Warnings

### WR-01: `_lease_supported` swallows the engine-not-initialized error, hiding CR-01's root cause

**File:** `tests/test_parity_scheduler.py:79-87`, `tests/chaos/test_lease_zombie.py:239-246`

**Issue:**
The probe catches `NotImplementedError`/`TypeError` (→ unsupported) but then `except Exception:
return True`. When the standalone backend's engine is uninitialized (CR-01), the probe call
`fn(queues=[], ...)` raises `RuntimeError("Database not initialized")`, which is caught here and
reported as "leases supported = True". The subsequent real `_claim(...)` then raises the same
`RuntimeError` uncaught, so the failure surfaces far from its cause and reads as a lease bug rather
than a mode/engine misconfiguration. The blanket `except Exception` also masks any genuinely broken
lease implementation as "supported."

**Fix:** Narrow the catch so configuration/availability errors are not mistaken for "supported":

```python
    except NotImplementedError:
        return False
    except TypeError:
        return False
    # Old: except Exception: return True  # masks RuntimeError("Database not initialized")
    except RuntimeError:
        # Engine/config not ready — treat as unsupported in this run rather than
        # claiming support and then erroring on the real call.
        return False
    except Exception:
        return True
```

### WR-02: Django `@pytest.mark.postgres` cells run against in-memory SQLite, so "Django × Postgres" parity is not actually exercised on Postgres

**File:** `tests/test_atomic_scheduler.py:633-745`, `tests/test_parity_scheduler.py:134-167`; root cause `tests/settings.py:7-12`

**Issue:**
The Django test settings hardcode `ENGINE: django.db.backends.sqlite3`, `NAME: ":memory:"` and do
not read `DATABASE_URL`. Every `@pytest.mark.django_db` cell — including the new
`TestCronSemanticsHardeningPostgres` and `test_failover_postgres_real_backend` — therefore executes
against in-memory SQLite even on the PG rail where `DATABASE_URL`/`SQLERY_TEST_PG_URL` point at
postgres:15. The docstrings assert these prove the invariant "on Postgres' MVCC / row-lock
semantics" (`test_atomic_scheduler.py:641-643`), but the Django ORM never touches Postgres. The
single-fire CAS happens to be engine-independent so the assertions still hold, but the
Postgres-specific claim is false and the cell does not add Postgres coverage over the existing
SQLite cell. (Pre-existing settings issue, surfaced by the new docstring claims — flagging because
it undermines the phase's stated parity goal.)

**Fix:** Make `tests/settings.py` select Postgres when `DATABASE_URL` is a postgres URL (e.g. via a
small parse of `os.environ.get("DATABASE_URL")`), or downgrade the docstrings to state these run on
SQLite and are engine-independent. The standalone × PG cells (`test_core_standalone.py`) do bind a
real PG engine and are unaffected.

### WR-03: `--collect-only` precheck does not guarantee the *standalone-relevant* cells are collected

**File:** `.github/workflows/test.yml:108-111`

**Issue:**
The precheck `pytest -m postgres --co -q <files>` only asserts the collection is non-empty (exit 5 →
fail). Because it includes `test_atomic_scheduler.py` and `test_parity_scheduler.py`, the
collection is non-empty even if the only collected items are the Django-mode cells (which are the
ones that misbehave per CR-01). So the "missing/empty standalone cell" guard can pass while the
actual standalone-backend cells (`TestStandaloneAdvanceScheduledTaskPostgres`,
`TestStandaloneLeaseFailoverPostgres`) are absent or renamed. The gate is weaker than its comment
("rejects an empty collection ... so a missing/skipped standalone PG cell fails the job too") claims.

**Fix:** After scoping per CR-01, the precheck collects only the standalone cells, which restores the
intended meaning. If the broad selection is kept, assert collection of the specific standalone
classes by name instead of a bare non-empty check.

### WR-04: Lease takeover assertions accept an empty/None result on a path that should always claim

**File:** `tests/chaos/test_lease_zombie.py:280, 333, 349`, `tests/test_parity_scheduler.py:131, 164`

**Issue:**
Assertions use the pattern `assert "chaos-q" in (first or [])`. When `claim_queue_leases` returns
`None` (or `[]`) on a path that is expected to succeed (e.g. `TestLeaseExpiry` line 274 after a real
1.5s sleep, or the failover takeover at line 279), the `(x or [])` coalescing turns a wrong-but-
non-raising `None`/empty return into a clean `AssertionError` — which is acceptable — but it also
means a backend that silently returns `None` everywhere produces a generic membership failure with
no diagnostic. More importantly, `TestLeaseExpiry.test_expired_lease_can_be_taken_over`
(lines 274-280) uses a real `time.sleep(1.5)` to age a 1s lease, contradicting the phase's stated
"no real TTL sleeps" convention and adding 1.5s of wall-clock per run on the default rail. The new
parity cells correctly use PAST `expires_at` writes instead; this older chaos cell should follow the
same pattern.

**Fix:** Prefer forcing expiry via a PAST `expires_at` write (as the new parity cells do) over
`time.sleep`, and add a message to the takeover assertions:

```python
    # Old: time.sleep(1.5); second = _claim(...)
    # New: force expiry without a wall-clock sleep (mirror the parity cells).
    DaemonLease.objects.filter(queue_name="chaos-q").update(
        expires_at=timezone.now() - timedelta(seconds=5)
    )
    second = _claim(backend, "chaos-q", "daemon-b", lease_secs=10)
    assert "chaos-q" in (second or []), "expired lease must be re-claimable by daemon-b"
```

## Info

### IN-01: Unused local variables in pre-existing scheduler tests

**File:** `tests/test_atomic_scheduler.py:149-150, 299-301`

**Issue:** `locked_task` is assigned and never used in `claim_task` and `hold_lock`. Harmless but
dead. (Pre-existing, not introduced this phase; noting since the file was touched.)

**Fix:** Prefix with `_` or drop the binding: `ScheduledTask.objects.select_for_update(skip_locked=True).get(id=task.id)`.

### IN-02: `prior_scheduled` assigned but unused in drift tests

**File:** `tests/test_atomic_scheduler.py:720`, `tests/test_core_standalone.py` (drift loops)

**Issue:** `prior_scheduled = task.next_run_at` is immediately copied into `last_next`/`last_scheduled`
and never read again. Minor readability noise.

**Fix:** Remove the intermediate or fold into the loop seed: `last_next = task.next_run_at`.

---

_Reviewed: 2026-06-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
