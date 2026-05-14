---
phase: 03-testing-ci
plan: 04
subsystem: testing
tags: [unit-tests, backends, django, sqlalchemy]
requires:
  - Plan 03-01 (D-02-07-1 migration fix for pytest-django setup_databases)
provides:
  - tests/unit/test_sqlalchemy_backend_sync.py (sync SQLAlchemyBackend coverage)
  - tests/unit/test_django_backend.py (DjangoBackend coverage)
affects:
  - pyproject.toml (pythonpath + postgres marker)
  - src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py (re-applied 03-01 fix)
tech_stack_added: []
patterns:
  - Method-by-method backend coverage with @pytest.mark.django_db / temp-file SQLite
key_files_created:
  - tests/unit/__init__.py
  - tests/unit/test_sqlalchemy_backend_sync.py
  - tests/unit/test_django_backend.py
key_files_modified:
  - pyproject.toml
  - src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py
decisions:
  - "Used module-level engine monkey-patch for SQLAlchemyBackend (real __init__ takes no engine arg)"
  - "xfail two TTL tests for pre-existing naive/aware datetime bug in fastapi_sqlery/backend.py (out of scope)"
  - "Postgres-only SKIP LOCKED test scaffolded with @pytest.mark.postgres for Plan 03-07"
metrics:
  duration_min: ~25
  completed_date: 2026-05-14
---

# Phase 3 Plan 04: Backend Unit Tests Summary

Direct unit coverage for TEST-08 (sync `SQLAlchemyBackend`) and TEST-09 (`DjangoBackend`) — two real-DB test suites driving each backend method-by-method.

## What was built

- **`tests/unit/test_sqlalchemy_backend_sync.py`** — 68 collected tests (66 passed, 2 xfailed) across 8 test classes (`TestEnqueueAndClaim`, `TestStatusTransitions`, `TestRetryAndTTL`, `TestWorkerLifecycle`, `TestScheduledTasks`, `TestRegistry`, `TestCleanup`, `TestMiscMethods`). Per-test temp-file SQLite engine (not `:memory:`) created via `tmp_path / "db.sqlite"`; SQLModel.metadata.create_all populates schema. Module-level `_engine` is monkey-patched so `SQLAlchemyBackend()`'s `get_session` closure resolves to the per-test engine.
  - **Coverage: 95%** of `src/sqlery/fastapi_sqlery/backend.py` (420 stmts, 20 miss). Plan target: ≥ 80%.

- **`tests/unit/test_django_backend.py`** — 73 collected tests (71 passed, 2 skipped on SQLite, 1 deselected via `-m "not postgres"`) across 9 classes (the eight above plus a `TestMiscMethods` superset and a single postgres placeholder). Uses `@pytest.mark.django_db`; `DjangoBackend()` reads from Django settings (no constructor args).
  - **Coverage: 91%** of `src/sqlery/django_sqlery/backend.py` (367 stmts, 32 miss). Plan target: ≥ 80%.

## How to run

```bash
uv run pytest tests/unit/test_sqlalchemy_backend_sync.py -v \
  --cov=sqlery.fastapi_sqlery.backend --cov-report=term --cov-fail-under=80

uv run pytest tests/unit/test_django_backend.py -v -m "not postgres" \
  --cov=sqlery.django_sqlery.backend --cov-report=term --cov-fail-under=80
```

## Acceptance criteria

| Criterion | Plan 03-04 target | Result |
|-----------|-------------------|--------|
| All 8 test classes (SQLAlchemy) | required | done (8 classes incl. TestMiscMethods) |
| All 8 test classes (Django) | required | done (8 classes incl. TestMiscMethods + 1 PG placeholder) |
| SQLAlchemy coverage ≥ 80% | required | 95% |
| Django coverage ≥ 80% (excl. PG branches) | required | 91% |
| Temp-file SQLite (not `:memory:`) | required | done — `tmp_path / "db.sqlite"` |
| `ConcurrentModificationError` asserted | required | done — `TestStatusTransitions.test_concurrent_modification_raises` |
| `@pytest.mark.postgres` test exists | required | done — `test_select_for_update_skip_locked_postgres_branch` |
| Runs in < 15s | required | SQLAlchemy: ~1.1s, Django: ~0.55s |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SQLAlchemyBackend fixture signature**
- **Found during:** Task 1
- **Issue:** Plan's fixture used `SQLAlchemyBackend(engine=engine)` but the real `__init__` accepts no args; it closes over `database.get_session` which reads a module-level `_engine`.
- **Fix:** Fixture monkey-patches `sqlery.fastapi_sqlery.database._engine` for the test's lifetime, then instantiates `SQLAlchemyBackend()` with no args. Engine is disposed in teardown.
- **Files modified:** tests/unit/test_sqlalchemy_backend_sync.py
- **Commit:** c15b255

**2. [Rule 3 - Blocking issue] pytest-django could not import `tests.settings`**
- **Found during:** Task 1 (first `uv run pytest`)
- **Issue:** With no top-level `manage.py`, pytest-django's project autoscan failed; `DJANGO_SETTINGS_MODULE = tests.settings` couldn't be imported because `.` was not on `sys.path`.
- **Fix:** Added `pythonpath = ["."]` and a `markers = ["postgres: ..."]` declaration to `[tool.pytest.ini_options]`.
- **Files modified:** pyproject.toml
- **Commit:** c15b255

**3. [Rule 3 - Blocking issue] Worktree base lacked Plan 03-01's D-02-07-1 fix**
- **Found during:** Task 2 (first `pytest tests/test_models.py`)
- **Issue:** Migration `0023_restore_daemonlease.py` in the worktree still contained the unconditional `CreateModel(name='DaemonLease', ...)` body. On a clean SQLite test DB this crashes pytest-django's `setup_databases` with `OperationalError: table "sqlery_daemon_lease" already exists` — 0020 creates the table, 0022 doesn't actually drop it (filename intent vs operations mismatch), and 0023 tries to create it again. Without this fix NO `@pytest.mark.django_db` test can run in this worktree.
- **Fix:** Re-applied Plan 03-01's resolution — reduced 0023 to `operations = []` with a module docstring explaining why the file is retained as a graph node. Identical content to main-branch commit 5a051e2.
- **Files modified:** src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py
- **Commit:** 2041f4e

### Known limitations documented as xfail/skip

**4. [scope: out] Pre-existing naive/aware datetime bug in `fastapi_sqlery/backend.py`**
- `get_expired_ttl_jobs` and `release_claimed_job` mix SQLite-naive `created_at`/`started_at` with `datetime.now(UTC)` (aware). Two tests are marked `@pytest.mark.xfail(raises=TypeError, strict=False)` rather than ignored; one worker-lifecycle test exercises the safe (started_at=None) branch.
- This bug pre-existed in the worktree base and is **out of scope** for this plan; should be filed as a separate fix (Rule 1) by whichever plan owns `fastapi_sqlery/backend.py` correctness.

**5. [scope: out] SQLite JSONField `contains` lookup unsupported**
- `count_running_with_tag` and `count_started_with_tag_since` use `tags__contains=[tag]` which Django rejects on SQLite (`NotSupportedError`). Both tests `pytest.skip()` on this error. Postgres mirror tests in Plan 03-07 will cover the real semantics.

## Threat Mitigations Verified

| Threat ID | Mitigation Plan | Result |
|-----------|-----------------|--------|
| T-03-07 (Tampering: optimistic-locking race) | Explicit ConcurrentModificationError assertion in DjangoBackend tests | `TestStatusTransitions.test_concurrent_modification_raises` passes — sets a stale `version` and asserts `mark_running()` raises |
| T-03-08 (Repudiation: "all 30+ methods covered") | Coverage threshold gates the claim; 80% per-file | SQLAlchemy 95%, Django 91% — both exceed the 80% gate |

## Commits

| Hash | Subject |
|------|---------|
| c15b255 | test(03-04): unit tests for sync SQLAlchemyBackend (TEST-08) |
| 2041f4e | test(03-04): unit tests for DjangoBackend (TEST-09) |

## Self-Check: PASSED

- `tests/unit/__init__.py` — FOUND
- `tests/unit/test_sqlalchemy_backend_sync.py` — FOUND
- `tests/unit/test_django_backend.py` — FOUND
- Commit `c15b255` — FOUND in worktree history
- Commit `2041f4e` — FOUND in worktree history
- Verifying commands rerun green at SUMMARY time (66 passed / 2 xfailed for SQLAlchemy; 71 passed / 2 skipped for Django).
