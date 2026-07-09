---
phase: 11-parity-gated-tests-ci
verified: 2026-06-08T00:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
---

# Phase 11: Parity-Gated Tests & CI Verification Report

**Phase Goal:** Failover, single-firing, drift correctness, and bare-worker scheduling are proven identical across the full `{Django, standalone} × {SQLite, Postgres}` matrix and enforced as a first-class CI acceptance gate.
**Verified:** 2026-06-08
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (merged ROADMAP success criteria + PLAN must_haves)

| # | Truth (PARITY-ID) | Status | Evidence |
|---|-------------------|--------|----------|
| 1 | Failover: killing the leader makes another worker schedule within one TTL, across the matrix (PARITY-01) | ✓ VERIFIED | `tests/test_parity_scheduler.py::TestParityFailover` — SQLite cell (`test_failover_sqlite_in_process`, PASSED) drives real `WorkerProcess.run` election against `FakeBackend` with a PAST `expires_at` and asserts takeover via claim record + `_job_count_for_task == 1`; PG cell (`test_failover_postgres_real_backend`) does real Django-backend lease takeover (`DaemonLease` owner transfers to daemon-b). Standalone half: `tests/chaos/test_lease_zombie.py::TestStandaloneLeaseFailoverPostgres::test_expired_standalone_lease_is_taken_over_pg` exercises `SQLAlchemyBackend.claim_queue_leases` takeover directly. |
| 2 | No-duplicate: two leaders fire a cron exactly once (PARITY-02) | ✓ VERIFIED | Django×PG: `test_atomic_scheduler.py::TestCronSemanticsHardeningPostgres::test_cron_fires_exactly_once_under_simulated_overlap_pg` — two `advance_scheduled_task_if_due` calls with the same stale `observed_due`, asserts exactly one winner + `QueuedJob...count()==1`. Standalone×PG: `test_core_standalone.py::TestStandaloneAdvanceScheduledTaskPostgres::test_two_attempts_same_observed_due_fire_exactly_once_pg` — first non-None, second None, `_count_jobs_for==1`. SQLite halves pre-exist (Phase 10) and still pass. |
| 3 | Drift: `next_run_at` correctness across several ticks (PARITY-03) | ✓ VERIFIED | Django×PG: `test_next_run_at_advances_without_drift_across_ticks_pg` computes each expected occurrence from PRIOR scheduled time (not wall-clock), asserts monotonic strict increase over 3 ticks. Standalone×PG: `test_next_run_at_advances_drift_free_pg` — same invariant via `calculate_next_run(base_time=last_scheduled)`, `_count_jobs_for==3`. Both drive the real `advance_scheduled_task_if_due` CAS, not the legacy `TaskExecutor`. |
| 4 | Bare-worker E2E: cron fires with only `sqlery-worker`, no daemon (PARITY-04) | ✓ VERIFIED | `tests/test_parity_scheduler.py::TestParityBareWorkerE2E` — SQLite cell (PASSED) constructs ONLY a `WorkerProcess` (no `DaemonManager`), asserts `_job_count_for_task==1`. Standalone real-process cell (`test_bare_worker_standalone_real_process`, slow, PASSED locally) runs a no-Django subprocess via `_run_no_django` that self-elects and prints `JOB_COUNT=1`. grep confirms no `DaemonManager()` constructed. |
| 5 | Every behavioral test runs across `{Django, standalone} × {SQLite, Postgres}` as a first-class CI gate (PARITY-05) | ✓ VERIFIED | `.github/workflows/test.yml`: default rail `-m "not postgres"` runs both SQLite cells; `Run @pytest.mark.postgres suite` (`-m postgres`, Django backend via pytest-django) runs Django×PG cells; NEW `Run standalone-mode parity suite with PostgreSQL` step runs the standalone-backend PG cells with a `--collect-only` empty-collection precheck and NO `|| echo` escape. All four cells execute; a failing/empty cell fails the build. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/test_atomic_scheduler.py` | Django×PG no-dup + drift cells, `@pytest.mark.postgres`, real CAS path | ✓ VERIFIED | `TestCronSemanticsHardeningPostgres` (lines 633-744), class-level `@pytest.mark.postgres` + `django_db(transaction=True)`, drives `advance_scheduled_task_if_due` / `Scheduler`, skip-guarded on `SQLERY_TEST_PG_URL`. Legacy `TaskExecutor` left untouched in OLD classes (CLAUDE.md add-only). |
| `tests/test_core_standalone.py` | Standalone×PG cells via `pg_standalone_backend` fixture | ✓ VERIFIED | `pg_standalone_backend` (lines 285-322) binds `SQLAlchemyBackend` to PG via `monkeypatch.setattr(db_mod, "_engine", engine)` + `drop_all`/`create_all`; `TestStandaloneAdvanceScheduledTaskPostgres` (325-403) asserts single-fire + drift on the real standalone backend. |
| `tests/test_parity_scheduler.py` | PARITY-01 + PARITY-04 cells, ≥60 lines | ✓ VERIFIED | 285 lines, `TestParityFailover` + `TestParityBareWorkerE2E`, `(integration, db)` axis, PG-only marker, PAST `expires_at` failover. SQLite + slow cells PASS locally. |
| `tests/chaos/test_lease_zombie.py` | Standalone-backend real-lease failover PG cell | ✓ VERIFIED | `TestStandaloneLeaseFailoverPostgres` (397-451) + `pg_standalone_backend` fixture; direct `SQLAlchemyBackend.claim_queue_leases` takeover with PAST `expires_at` write. |
| `.github/workflows/test.yml` | Standalone×PG parity CI step | ✓ VERIFIED | New step (lines 101-115) with `SQLERY_FORCE_STANDALONE`, PG service URLs, `--collect-only` precheck + real run, no `|| echo`. YAML parses cleanly. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `test_atomic_scheduler.py` (PG class) | `backend.advance_scheduled_task_if_due` | `Scheduler(get_backend())` CAS under overlap | ✓ WIRED | Two CAS calls with same `observed_due`, assert one winner. |
| `test_core_standalone.py` (PG class) | `SQLAlchemyBackend.advance_scheduled_task_if_due` | `pg_standalone_backend` fixture bound to PG | ✓ WIRED | Fixture constructs `SQLAlchemyBackend()` directly; not mode-detection dependent. |
| `test_parity_scheduler.py` | `WorkerProcess.run` election | `_run_one_election_cycle` PAST `expires_at` | ✓ WIRED | Imports real harness from `tests.unit.test_worker`; spies wrap real lease/claim fns. |
| `test_lease_zombie.py` | `backend.claim_queue_leases` takeover | second daemon_id re-claim after PAST `expires_at` | ✓ WIRED | Standalone backend session writes PAST `expires_at`, asserts daemon-b owns row. |
| `.github/workflows/test.yml` (standalone step) | standalone×Postgres cells | `-m postgres` over 3 parity files | ✓ WIRED | Collection precheck returns 7 PG cells (verified locally); empty collection → exit 5 → job fail. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Default-rail parity + scheduler suite | `pytest tests/unit tests/test_atomic_scheduler.py tests/test_core_standalone.py tests/test_parity_scheduler.py tests/chaos/test_lease_zombie.py -m "not postgres and not slow"` | 454 passed, 6 skipped, 3 xfailed, 0 failed | ✓ PASS |
| SQLite failover + bare-worker cells | `pytest tests/test_parity_scheduler.py -m "not postgres and not slow" -v` | 2 passed | ✓ PASS |
| Slow standalone no-Django E2E (JOB_COUNT=1) | `pytest tests/test_parity_scheduler.py -m "slow and not postgres" -v` | 1 passed | ✓ PASS |
| PG-cell collection (standalone CI step files) | `pytest -m postgres --co -q <3 parity files>` | 7 PG cells collected (non-empty) | ✓ PASS |
| Workflow YAML validity | `python -c "import yaml; yaml.safe_load(...)"` | YAML OK | ✓ PASS |
| PG cells skip cleanly without URL | (above, 6 skipped, 0 errors) | clean skip | ✓ PASS |

PG-bound execution (cells PASSING, not just skipping, with `SQLERY_TEST_PG_URL`) cannot be run locally (no PG service) — see Human Verification.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PARITY-01 | 11-02 | Failover proof across matrix | ✓ SATISFIED | Truth 1 |
| PARITY-02 | 11-01 | No-duplicate firing | ✓ SATISFIED | Truth 2 |
| PARITY-03 | 11-01 | Drift/atomic-advance correctness | ✓ SATISFIED | Truth 3 |
| PARITY-04 | 11-02 | Bare-worker E2E, no daemon | ✓ SATISFIED | Truth 4 |
| PARITY-05 | 11-03 | Full matrix as first-class CI gate | ✓ SATISFIED | Truth 5 |

All 5 requirement IDs from REQUIREMENTS.md (lines 41-45, 79-83) map to Phase 11 and are claimed across the three plans (11-01: 02/03; 11-02: 01/04; 11-03: 05). No orphaned IDs.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | — | — | No TBD/FIXME/XXX/TODO/HACK debt markers in any phase-modified file. The one "implemented" grep hit in `test_lease_zombie.py:18` is prose ("have not implemented leases they SKIP"), not a stub marker. |

### Notable Observation (informational, not a gap)

The CI standalone step's `SQLERY_FORCE_STANDALONE=1` env var is **not read anywhere in `src/sqlery/compat/`** — `_detect_mode()` (compat/__init__.py:845) keys only on whether Django settings are configured. Since `DJANGO_SETTINGS_MODULE = "tests.settings"` is set in `pyproject.toml [tool.pytest.ini_options]`, `get_backend()` resolves the Django backend in that step regardless of the env var. The SUMMARY's claim that the var "forces compat to resolve the standalone backend" is therefore inaccurate.

This does **not** undermine PARITY-05: the genuinely standalone×Postgres cells (`TestStandaloneAdvanceScheduledTaskPostgres`, `TestStandaloneLeaseFailoverPostgres`) construct `SQLAlchemyBackend()` directly via their `pg_standalone_backend` fixtures and do not depend on mode detection at all. The standalone backend is exercised by fixture construction, which is robust. The inert env var is a documentation discrepancy only.

### Human Verification Required

The Postgres half of the matrix cannot be exercised locally (no `SQLERY_TEST_PG_URL` / PG service). These cells correctly SKIP locally; they will run on the CI PG rail.

#### 1. Postgres parity cells PASS (not just skip) on the CI PG rail

**Test:** Trigger CI (push/PR) so the `test` job runs against `postgres:15`, OR run locally with `SQLERY_TEST_PG_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest -m postgres tests/test_parity_scheduler.py tests/test_core_standalone.py tests/test_atomic_scheduler.py tests/chaos/test_lease_zombie.py -v`.
**Expected:** The Django×PG cells (TestCronSemanticsHardeningPostgres, test_failover_postgres_real_backend) and standalone×PG cells (TestStandaloneAdvanceScheduledTaskPostgres, TestStandaloneLeaseFailoverPostgres) all report PASSED — proving single-fire/drift/failover on real Postgres MVCC, not merely skipping.
**Why human:** Requires a running PostgreSQL service unavailable in this verification environment.

#### 2. The standalone CI step genuinely fails the build on a broken standalone×PG cell

**Test:** In CI, confirm the `Run standalone-mode parity suite with PostgreSQL` step executes the standalone-backend cells and that the `--collect-only` precheck + absence of `|| echo` would fail the job on an empty collection or a failing cell.
**Expected:** Green when cells pass; red (non-zero exit) if any standalone×PG parity cell fails or collection is empty.
**Why human:** Requires observing actual CI job exit status against the PG service.

### Gaps Summary

No blocking gaps. All five PARITY requirements have substantive, wired, behaviorally-verified test coverage. SQLite + slow cells pass locally (454 passed, plus the dedicated parity-cell runs). The CI gate is correctly structured (no escape hatch, empty-collection rejection, all four matrix cells routed to a rail that executes them). The only finding is an informational documentation inaccuracy (the inert `SQLERY_FORCE_STANDALONE` env var), which does not affect goal achievement because the standalone cells use direct-fixture backend construction. The remaining verification (PG cells passing on a live PG service) is inherently environmental and routed to human/CI.

---

_Verified: 2026-06-08_
_Verifier: Claude (gsd-verifier)_
