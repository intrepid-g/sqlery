---
phase: 10-harden-cron-semantics
plan: 04
subsystem: scheduler-tests
tags: [cron, atomicity, cas, drift, jitter, tests, CRON-01, CRON-02, CRON-03, CRON-04]
requires:
  - "backend.advance_scheduled_task_if_due (Plan 01)"
  - "core.scheduler.Scheduler hardened firing path (Plan 03)"
  - "scheduler_jitter_seconds / SCHEDULER_JITTER_SECONDS config key (Plan 02)"
provides:
  - "Django-mode behavioral proof of CRON-01..04 against the hardened core Scheduler (SQLite)"
  - "Standalone SQLAlchemyBackend.advance_scheduled_task_if_due DB-correctness proof (SQLite)"
  - "Reusable standalone_backend pytest fixture in tests/test_core_standalone.py"
affects: []
tech-stack:
  added: []
  patterns:
    - "Single-fire proven via CAS on observed next_run_at — engine-independent, runs on SQLite (not @skip_on_sqlite)"
    - "Drift asserted by comparing fired next_run_at to calculate_next_run(base_time=prior scheduled time)"
    - "Jitter asserted by patched time.sleep argument bounds in [0, jitter] (no wall-clock timing dependence)"
    - "Standalone backend tested directly via per-test temp-file SQLite engine fixture"
key-files:
  created: []
  modified:
    - "tests/test_atomic_scheduler.py"
    - "tests/test_core_standalone.py"
decisions:
  - "Tests drive sqlery.core.scheduler.Scheduler (the hardened runtime path), NOT the legacy sqlery.executor.TaskExecutor the plan referenced — see Deviations Rule 1"
  - "Single-fire and drift tests run on SQLite because the CAS makes exactly-once engine-independent; the 4 pre-existing Postgres-only concurrency tests stay skipped on SQLite"
  - "Jitter value injected by patching scheduler._get_jitter_seconds (DjangoConfig.get reads DJANGO_SQL_JOBS only, per 10-02-SUMMARY), keeping the test independent of the config-resolution surface"
metrics:
  duration: ~20m
  completed: 2026-06-08
  tasks: 2
  files: 2
---

# Phase 10 Plan 04: CRON-01..04 Behavioral Proof Summary

Added DB-backed behavioral tests proving the four hardened CRON behaviors are real: atomic single-firing under simulated two-leader overlap (running on SQLite via the CAS), drift-free next_run_at advance from the scheduled time, the bounded jitter knob, and interval/once non-regression — in Django mode against the hardened `core.scheduler.Scheduler`, and directly against the standalone `SQLAlchemyBackend.advance_scheduled_task_if_due`.

## What Was Built

### Task 1 — Django CRON-01..04 behavioral tests (`tests/test_atomic_scheduler.py`)

New class `TestCronSemanticsHardening` (6 methods), `@pytest.mark.django_db(transaction=True)`. It drives `sqlery.core.scheduler.Scheduler` wired to `get_backend()` (DjangoBackend) — the actual hardened runtime path — via a `_scheduler()` static helper, and a `_make_due_cron_task()` helper that pins `next_run_at` to a fixed past time via a queryset `.update()` so the model's save()-time recalculation cannot clobber the pinned scheduled time.

- `test_cron_fires_exactly_once_under_simulated_overlap` — reads `observed_due` once, makes two `advance_scheduled_task_if_due` attempts with that same (now-stale-for-the-second) observed_due; asserts exactly one wins and `QueuedJob.objects.filter(scheduled_task_id=...).count() == 1`. Runs on SQLite (NOT `@skip_on_sqlite`).
- `test_cron_fires_exactly_once_under_threaded_overlap` — two threads call `run_due_tasks()` on the same due task; asserts exactly one QueuedJob (durable CAS invariant, also on SQLite).
- `test_next_run_at_advances_without_drift_across_ticks` — over several ticks asserts `task.next_run_at == calculate_next_run(cron, base_time=prior scheduled time)` (computed from the scheduled time, not now), monotonic and drift-free, settling to a future occurrence.
- `test_far_behind_task_clamps_to_future_occurrence` — a task pinned 365 days behind fires exactly once (no missed-tick replay) and advances to a future `next_run_at`.
- `test_scheduler_jitter_seconds_respected` — patches `scheduler._get_jitter_seconds` and `sqlery.core.scheduler.time.sleep`; with jitter `0.5` asserts a sleep with arg in `[0, 0.5]`; with `0` asserts sleep is NOT called.
- `test_interval_and_once_not_regressed` — interval task re-advances `next_run_at` by ~its interval (290–310s window around now+300s); once task ends `enabled=False, next_run_at=None`.

### Task 2 — Standalone backend correctness tests (`tests/test_core_standalone.py`)

New class `TestStandaloneAdvanceScheduledTask` (3 methods) plus a `standalone_backend` pytest fixture (per-test temp-file SQLite engine, mirroring `tests/unit/test_sqlalchemy_backend_sync.py`), and helpers `_make_due_scheduled_task`, `_job_kwargs_for` (10-01-SUMMARY field mapping), `_count_jobs_for`.

- `test_winning_cas_creates_job_and_advances` — matching `observed_due` returns a job, advances `next_run_at`, exactly one QueuedJob.
- `test_stale_observed_due_returns_none_no_job` — non-matching `observed_due` returns None, zero jobs, row unchanged.
- `test_two_attempts_same_observed_due_fire_exactly_once` — first returns a job, second returns None, exactly one QueuedJob, `next_run_at == new_next_run`.

## Which tests run on SQLite vs require Postgres

- ALL 9 new tests (6 Django + 3 standalone) run on the default SQLite lane and pass. The single-fire and drift proofs are engine-independent by design (CAS on observed `next_run_at`).
- No new Postgres-only tests were added. The 4 pre-existing `@skip_on_sqlite` concurrency tests (`TestAtomicSchedulerClaiming` / `TestAtomicSchedulerPerformance`) remain skipped on SQLite, unchanged.
- The full `{Django, standalone} × {SQLite, Postgres}` parity matrix is explicitly deferred to Phase 11 (per plan scope).

## Fixture added for the standalone backend

`standalone_backend` (function-scoped) in `tests/test_core_standalone.py`: creates a temp-file SQLite engine, `SQLModel.metadata.create_all(engine)`, monkeypatches `sqlery.fastapi_sqlery.database._engine`, instantiates `SQLAlchemyBackend()`, disposes the engine on teardown. No new dependencies (reuses sqlalchemy/sqlmodel/pytest already in the dev/standalone extras).

## Verification

- `pytest tests/test_atomic_scheduler.py -k "CronSemanticsHardening ..." -v` → 6 passed.
- `pytest tests/test_core_standalone.py -k "StandaloneAdvanceScheduledTask ..." -v` → 3 passed.
- `pytest tests/test_atomic_scheduler.py tests/test_core_standalone.py -q` → 17 passed, 4 skipped (no regressions).
- Regression: `pytest tests/test_scheduler_drift_jitter.py tests/test_scheduler_compat.py -q` → 58 passed.
- `black --diff` on both files: the only remaining flagged hunks are PRE-EXISTING (test_atomic_scheduler.py lines 32/143/293 single-quote/wrapping; test_core_standalone.py line 13 blank-line spacing). All lines this plan added are black-clean (verified via `black --diff`).
- No new dependencies (CLAUDE.md "no new deps" satisfied); imports placed at top of each added block per the user's no-inline-imports preference, except the two intentionally-localized optional imports inside `_count_jobs_for` / the fixture that mirror the existing standalone test file's deliberate lazy-import style for django-optional code.

## TDD Gate Compliance

Plan `type: tdd`. Both tasks are test-only and target behavior already implemented in Plans 01/03 (the atomic primitive and the hardened firing path). The TDD cycle here is "write the behavioral proof against the already-landed implementation": the tests were authored, run, and confirmed to PASS against the existing hardened code. There is no separate RED (no-implementation) gate because the implementation under test is a dependency (Plans 01/03), not produced by this plan. The first run did fail (RED-equivalent) for a real reason — see Deviation Rule 1 — exposing that the plan-referenced `TaskExecutor` was the wrong (legacy, unhardened) scheduler; the tests were corrected to target the hardened path and then passed (GREEN). No REFACTOR commit was needed beyond black-clean formatting folded into the same commits.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Tests must drive `core.scheduler.Scheduler`, not the legacy `sqlery.executor.TaskExecutor`**
- **Found during:** Task 1 first test run (RED).
- **Issue:** The plan instructed reuse of `TaskExecutor` (`from sqlery.executor import TaskExecutor`), calling it "the Scheduler alias used in this file." That name resolves to the LEGACY Django `sqlery.django_sqlery._executor_impl.TaskExecutor`, which the Plan 01/03 rework did NOT touch: it still uses `SELECT FOR UPDATE SKIP LOCKED` (skipped on SQLite), computes `next_run_at` from wall-clock `now()` (not drift-corrected), never calls `advance_scheduled_task_if_due`, and applies no jitter. It also lacks `_get_jitter_seconds`, `.backend`, and `.calculate_next_run` in the hardened form — the first run failed with `AttributeError: ... does not have the attribute '_get_jitter_seconds'`. Testing through it would NOT prove CRON-01..04. The hardened path that the daemon (`daemon.py:354`) and the Phase 9 worker-elected scheduler (`worker.py:506`) actually run at runtime is `sqlery.core.scheduler.Scheduler` wired to `get_backend()`.
- **Fix:** Rewrote `TestCronSemanticsHardening` to build `Scheduler(backend=get_backend())` (DjangoBackend under the test harness) via a `_scheduler()` helper, and added `from sqlery.compat import get_backend` / `from sqlery.core.scheduler import Scheduler` at module top. A class docstring documents why. The single-fire test asserts directly against `backend.advance_scheduled_task_if_due` (the CAS), so it is engine-independent and runs on SQLite.
- **Files modified:** `tests/test_atomic_scheduler.py`.
- **Commit:** e6830f2.

**2. [Style] black-clean only the lines this plan added; pre-existing black failures left untouched (out of scope)**
- **Found during:** Task 1 and Task 2 verification.
- **Issue:** Both files FAIL `black --check` in their pristine state (test_atomic_scheduler.py single-quote/wrapping at lines 32/143/293; test_core_standalone.py blank-line spacing at line 13). These are pre-existing and not caused by this plan.
- **Fix/Decision:** Did NOT reformat pre-existing lines (out of scope per executor scope boundary; conflicts with the user's global no-blanket-line-replacement rule). Collapsed this plan's own added multi-line calls to the single-line form black prefers so every added line is black-clean. `black --diff` confirms only pre-existing hunks remain. Mirrors the documented 10-02 / 10-03 deviation.
- **Files modified:** none beyond the planned test additions.
- **Commit:** folded into e6830f2 (Task 1) and 0a77c1e (Task 2).

## Threat Surface

Per the plan threat register:
- T-10-08 (untested race claim) — mitigated: the single-fire test directly exercises the CAS under simulated two-leader overlap (sequential + threaded) on SQLite and asserts exactly-once.
- T-10-09 (flaky timing-based jitter) — mitigated: jitter asserted via patched `time.sleep` argument bounds, not wall-clock duration.
- T-10-SC (slopsquat/install) — mitigated: no new packages; reuses already-declared pytest/pytest-django/sqlmodel/sqlalchemy test deps. No install task.

No new security surface introduced (test-only changes).

## Self-Check: PASSED

- FOUND: tests/test_atomic_scheduler.py (TestCronSemanticsHardening, 6 methods)
- FOUND: tests/test_core_standalone.py (TestStandaloneAdvanceScheduledTask, 3 methods + standalone_backend fixture)
- FOUND commit e6830f2 (Task 1)
- FOUND commit 0a77c1e (Task 2)
