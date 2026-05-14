---
phase: 03-testing-ci
verified: 2026-05-14T00:00:00Z
status: gaps_found
score: 9/12 TEST requirements verified; 3/5 ROADMAP success criteria verified
overrides_applied: 0
gaps:
  - truth: "Phase 03 test suite passes when run locally"
    status: failed
    reason: "3 newly-landed unit tests fail; 1 chaos test module fails to collect — the postgres CI step has no tolerance and will be broken by the collection error."
    artifacts:
      - path: "tests/unit/test_django_backend.py"
        issue: "TestEnqueueAndClaim::test_claim_job_returns_queued_then_running and ::test_claim_job_none_when_empty fail with `RuntimeError: Database access not allowed, use the 'django_db' mark`. The tests touch the real Django ORM but are not decorated with @pytest.mark.django_db (or do not route through FakeBackend as the unit-test design requires)."
      - path: "tests/unit/test_worker.py"
        issue: "TestForkLifecycle::test_parent_branch_records_child_pid_and_waits fails — `_fork_and_execute` calls `close_old_connections()` which hits Django's DB-access guard. Either monkeypatch `close_old_connections` or apply the django_db mark."
      - path: "tests/chaos/test_property_based.py"
        issue: "Collection ImportError: `cannot import name 'serialize_job_arguments' from 'sqlery.utils'`. Pre-existing but unaddressed; .github/workflows/test.yml line 83 (`uv run pytest -m postgres ...`) has NO `--ignore=tests/chaos/` and NO `|| echo` tolerance, so this single collection error will exit-1 the entire postgres CI rail and break TEST-11 verification in CI."
    missing:
      - "Fix or mark the 2 Django-backend tests (apply @pytest.mark.django_db, or refactor to use FakeBackend per the unit-test design)"
      - "Fix the test_worker.py fork-lifecycle test (monkeypatch close_old_connections)"
      - "Either fix tests/chaos/test_property_based.py (restore the missing serialize_job_arguments export, or update the test imports) OR add `--ignore=tests/chaos/test_property_based.py` to the `Run @pytest.mark.postgres suite` step in .github/workflows/test.yml"

  - truth: "Coverage gate enforces the Phase 3 Success Criterion #4 (Unit tests for core/worker.py, core/daemon.py with meaningful coverage)"
    status: partial
    reason: "Coverage gate is wired (pyproject + CI) but pinned at 13% with a [FOLLOWUP] tag. The ROADMAP/PLAN target is 70%. Plan 03-03 also reported worker.py at 45% and daemon.py at 28%, which is below any production threshold."
    artifacts:
      - path: "pyproject.toml:167"
        issue: "fail_under = 13 (not 70). Comment correctly documents this as a deviation due to 196 pre-existing test-collection errors."
      - path: ".github/workflows/test.yml:94"
        issue: "--cov-fail-under=13 (matches pyproject)."
    missing:
      - "Resolve the 196 test-collection errors so a true baseline can be measured"
      - "Raise fail_under toward 70 (or document a phase-level override accepting 13 as the v1 floor)"

  - truth: "All 12 mode×integration cells are covered by E2E tests (ROADMAP SC#2)"
    status: partial
    reason: "tests/integration/test_modes.py parametrizes 4 modes (daemon, subprocess, http-trigger, sync) × 2 integrations × 2 dbs. Lambda and async modes are covered by separate, sparser files (test_async_e2e.py: 2 tests; test_lambda_django.py: 1; test_lambda_standalone.py: 1). This does not reach the '12 mode-integration combinations' target with the same rigor as the parametrized matrix."
    artifacts:
      - path: "tests/integration/test_modes.py:34"
        issue: "MODES = ['daemon','subprocess','http-trigger','sync'] — lambda + async absent from the matrix."
    missing:
      - "Extend the test_modes.py parametrization to include lambda + async (or document that test_async_e2e.py and test_lambda_* files satisfy TEST-01/02 for those two modes)"

deferred:
  - truth: "Coverage threshold raised to 70%"
    addressed_in: "Phase 3 [FOLLOWUP] inside this phase — recorded in pyproject.toml and 03-08-SUMMARY.md"
    evidence: "pyproject.toml lines 161-166 explicitly tag this as [FOLLOWUP] pending resolution of the 196 test-collection errors. Per CONTEXT C, the 70% gate is a phase deliverable; the [FOLLOWUP] tag means it is acknowledged technical debt within this phase, not a later-phase scope item."
---

# Phase 3: Testing & CI — Verification Report

**Phase Goal:** Every execution mode has comprehensive test coverage (E2E, edge cases, unit tests) running in CI on both SQLite and PostgreSQL.
**Verified:** 2026-05-14
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### TEST-* Requirements Coverage

| #       | Requirement                                                              | Status      | Evidence                                                                                                                                                              |
| ------- | ------------------------------------------------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TEST-01 | E2E integration tests for each execution mode in Django (6 modes)        | ⚠️ PARTIAL  | tests/integration/test_modes.py covers 4/6 (daemon, subprocess, http-trigger, sync). Lambda → test_lambda_django.py (1 test). Async → test_async_e2e.py (2 tests).    |
| TEST-02 | E2E integration tests for each execution mode in standalone (6 modes)    | ⚠️ PARTIAL  | Same as TEST-01 standalone side; subprocess-standalone + http-trigger-standalone explicitly deferred to 02-08 in test_modes.py docstring.                             |
| TEST-03 | Edge cases: timeout, crash recovery, retry, concurrent workers           | ✓ VERIFIED  | tests/chaos/test_subprocess_chaos.py (7 tests, real subprocess), property-based tests in test_property_based.py (collection-broken — see gap).                        |
| TEST-04 | Zombie detection, stale heartbeat, lease expiry                          | ✓ VERIFIED  | tests/chaos/test_lease_zombie.py (7 tests). 03-06-SUMMARY claims 10/10 (likely class-method count vs def count discrepancy).                                          |
| TEST-05 | Unit tests for core/claiming.py                                          | ✓ VERIFIED  | tests/unit/test_claiming.py — 33 tests collected; 33 pass under `pytest --no-cov`.                                                                                    |
| TEST-06 | Unit tests for core/worker.py                                            | ⚠️ PARTIAL  | tests/unit/test_worker.py — 24 tests, **1 FAILS** (test_parent_branch_records_child_pid_and_waits — Django DB access guard).                                          |
| TEST-07 | Unit tests for core/daemon.py                                            | ✓ VERIFIED  | tests/unit/test_daemon.py — 27 tests pass.                                                                                                                            |
| TEST-08 | Unit tests for fastapi_sqlery/backend.py (SQLAlchemyBackend)             | ✓ VERIFIED  | tests/unit/test_sqlalchemy_backend_sync.py — 74 tests, all pass (some skipped).                                                                                       |
| TEST-09 | Unit tests for django_sqlery/backend.py (DjangoBackend)                  | ⚠️ PARTIAL  | tests/unit/test_django_backend.py — 74 tests, **2 FAIL** (test_claim_job_returns_queued_then_running, test_claim_job_none_when_empty — missing django_db mark).       |
| TEST-10 | Unit tests for webhooks.py                                               | ✓ VERIFIED  | tests/unit/test_webhooks.py — 36 pass + 1 xfail. 03-05 claims 100% coverage of webhooks.py.                                                                           |
| TEST-11 | PostgreSQL-specific tests in CI for all modes                            | ⚠️ PARTIAL  | `postgres` marker registered (pyproject.toml:146); test_modes.py db axis includes `pytest.param('postgres', marks=pytest.mark.postgres)`. `-m postgres` collects 18 tests. **BUT** the CI step that runs `-m postgres` will fail due to test_property_based.py collection error (no --ignore, no tolerance). |
| TEST-12 | CI workflow triggers fixed (master → main)                               | ✓ VERIFIED  | .github/workflows/test.yml lines 11+13: `branches: [ main ]` for both push and pull_request. No `master` references.                                                  |

### ROADMAP Success Criteria

| #   | Criterion                                                                                           | Status      | Evidence                                                                                                                       |
| --- | --------------------------------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 1   | CI runs on push to `main` (not `master`) and all test jobs pass green                               | ⚠️ PARTIAL  | Trigger fixed (PASS). Jobs green: NOT verified — 3 unit failures + 1 chaos collection error will fail CI today.                 |
| 2   | E2E tests for all 12 mode-integration combinations (6 modes × 2 backends)                           | ⚠️ PARTIAL  | 4 modes parametrized in test_modes.py (×2 integrations = 8 cells); lambda + async have separate single-test files.             |
| 3   | Edge cases cover timeout, crash recovery, retry, concurrent workers, zombie, stale heartbeat        | ✓ VERIFIED  | tests/chaos/ contains test_subprocess_chaos.py and test_lease_zombie.py with real subprocess + lease fixtures.                  |
| 4   | Unit tests for core/claiming.py, core/worker.py, core/daemon.py, both backends, webhooks.py         | ⚠️ PARTIAL  | All files exist with substantive tests. 3 fail. Per-module coverage low for worker.py (45%) and daemon.py (28%, per 03-03).     |
| 5   | PostgreSQL-specific test suite runs in CI and covers all modes (not just 2 files)                   | ⚠️ PARTIAL  | Marker + matrix exposed; CI step `Run @pytest.mark.postgres suite` exists but will fail on chaos collection error.              |

### Required Artifacts (spot checks)

| Artifact                                                            | Expected                                | Status      | Details                                                                                |
| ------------------------------------------------------------------- | --------------------------------------- | ----------- | -------------------------------------------------------------------------------------- |
| `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py`   | `operations = []` (no-op)               | ✓ VERIFIED  | Line 23: `operations = []`. Original CreateModel preserved as comments (lines 26-50).  |
| `.github/workflows/test.yml`                                        | trigger on main only; coverage step     | ✓ VERIFIED  | Lines 11,13 = main. Coverage step lines 85-94. Artifact upload lines 96-102.           |
| `pyproject.toml [tool.coverage.report] fail_under`                  | Set (target 70, baseline 13 acceptable) | ⚠️ PARTIAL  | `fail_under = 13` with [FOLLOWUP] comment. Plan target is 70.                          |
| `pyproject.toml markers` includes `postgres`                        | Registered                              | ✓ VERIFIED  | Line 146: `"postgres: requires a running PostgreSQL service..."`.                      |
| `pyproject.toml pythonpath`                                         | `["."]`                                 | ✓ VERIFIED  | Line 143: `pythonpath = ["."]`.                                                        |
| Phase 2 deps preserved: django>=5.2, aiosqlite, greenlet            | Present                                 | ✓ VERIFIED  | Lines 45, 57, 58; plus all-django/all extras.                                          |
| `tests/unit/conftest.py` with FakeBackend implementing the full ABC | Subclass of DatabaseBackend             | ✓ VERIFIED  | Line 201: `class FakeBackend(DatabaseBackend)`. 670 lines total.                       |
| `tests/integration/test_modes.py` db axis with `postgres` marker    | Present                                 | ✓ VERIFIED  | Line 44: `pytest.param("postgres", marks=pytest.mark.postgres)`.                       |
| `tests/chaos/test_worker_chaos.py` dated stub                       | Module-level skip                       | ✓ VERIFIED  | Line 1: `# #CLEANUP 2026-05-14: dead — superseded by ...`. `pytest.skip(...allow_module_level=True)`. |

### Behavioral Spot-Checks

| Behavior                                                                  | Command                                                                                            | Result                                            | Status      |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ----------- |
| Webhook + claiming unit tests pass                                        | `uv run pytest tests/unit/test_webhooks.py tests/unit/test_claiming.py --no-cov -q`                | 70 passed, 1 xfailed                              | ✓ PASS      |
| Worker/daemon/backend unit tests pass                                     | `uv run pytest tests/unit/test_worker.py tests/unit/test_daemon.py tests/unit/test_*backend*.py`   | 185 passed, 9 skipped, 2 xfailed, **3 failed**    | ✗ FAIL      |
| test_modes.py collection                                                  | `uv run pytest tests/integration/test_modes.py --collect-only`                                     | 16 tests collected                                | ✓ PASS      |
| `-m postgres` collects > 0 tests                                          | `uv run pytest -m postgres --collect-only`                                                         | 18 collected, **1 collection error** (test_property_based.py ImportError) | ✗ FAIL |

### Anti-Patterns Found

| File                                                  | Pattern                          | Severity   | Impact                                                                                                                   |
| ----------------------------------------------------- | -------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------ |
| `pyproject.toml:161-167`                              | `[FOLLOWUP]` debt marker         | ℹ️ INFO    | Explicitly referenced in 03-08-SUMMARY.md and tied to "fix the 196 test-collection errors" — meets the auditable-followup bar. |
| `tests/chaos/test_property_based.py`                  | ImportError on collection        | 🛑 BLOCKER | Will break the `Run @pytest.mark.postgres suite` step in CI (no --ignore, no `|| echo` tolerance).                       |
| `tests/chaos/test_worker_chaos.py`                    | Dated `#CLEANUP 2026-05-14` stub | ℹ️ INFO    | References historical reason, slated for Phase 4 cleanup. Acceptable under CLAUDE.md feedback_dead_code policy.          |

## Gaps Summary

Phase 03 makes significant progress: CI trigger is fixed (TEST-12), webhooks are at 100% coverage (TEST-10), claiming/daemon unit suites pass cleanly (TEST-05, TEST-07), backend unit tests are substantive (TEST-08, TEST-09 — 74 tests each), the migration 0023 no-op is in place, the chaos rebuild is real, the `postgres` marker is registered and exercised, and the coverage gate is wired (with an honest [FOLLOWUP] tag).

However, three concrete defects prevent declaring the phase complete:

1. **Three landed unit tests fail locally** (`test_django_backend.py` ×2, `test_worker.py` ×1). All three trip Django's `Database access not allowed` guard — the unit tests are touching the real ORM instead of FakeBackend / they are missing `@pytest.mark.django_db`. These are tests *written by Phase 03*, not legacy debt.

2. **`tests/chaos/test_property_based.py` fails to collect** (`ImportError: cannot import name 'serialize_job_arguments' from 'sqlery.utils'`). The new `Run @pytest.mark.postgres suite` CI step (line 83 of test.yml) has no `--ignore=tests/chaos/` and no `|| echo` tolerance — Plan 03-07 explicitly *removed* the exit-code-5 tolerance. This single collection error will fail the entire postgres rail on the next CI run.

3. **Coverage gate is 13%, not 70%.** This is acknowledged with [FOLLOWUP] but the gating value of Phase 3 SC#4 ("Unit tests exist … with meaningful coverage") is materially weakened. Plan 03-03 also showed worker.py at 45% and daemon.py at 28% — even if the 196 test-collection errors were fixed, those two modules need more tests.

**Recommendation:** **NEEDS GAP CLOSURE.** The 3 failing unit tests and the chaos collection error are localized fixes (decorator additions / one --ignore flag or restored export) and should be closed in a small follow-up before declaring Phase 3 done. The coverage threshold can remain a documented [FOLLOWUP] item provided the maintainer is OK ratifying 13% as the v1 floor.

---

_Verified: 2026-05-14_
_Verifier: Claude (gsd-verifier)_
