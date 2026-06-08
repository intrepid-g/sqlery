---
phase: 10-harden-cron-semantics
verified: 2026-06-08T00:00:00Z
status: passed
score: 8/8 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: null
  previous_score: null
---

# Phase 10: Harden Cron Semantics Verification Report

**Phase Goal:** Cron ticks fire exactly once and on schedule even under crashes and brief two-leader overlap, with no double-fire, skip, or drift.
**Verified:** 2026-06-08
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

Merged from ROADMAP success criteria (4) + PLAN frontmatter must-have truths (deduplicated). The 4 roadmap success criteria map 1:1 to CRON-01..04 and are the contract; PLAN truths add implementation detail and are folded into the same checks.

| #   | Truth (source)                                                                                                       | Status     | Evidence |
| --- | -------------------------------------------------------------------------------------------------------------------- | ---------- | -------- |
| 1   | (SC1/CRON-01) Enqueue and `next_run_at` advance happen atomically in one transaction — verified on BOTH backends     | ✓ VERIFIED | Standalone `backend.py:1019-1071`: one `_get_session()`, advance + `session.add(job)` + single `commit()`. Django `backend.py:690-697`: `with transaction.atomic():` wraps `.update()` CAS + `self.create_job()`. ABC `compat/__init__.py:541` is `@abstractmethod` forcing both impls (both instantiate). Test `test_winning_cas_creates_job_and_advances` passes. |
| 2   | (SC2/CRON-02) Next occurrence computed from scheduled time, not wall-clock `now`, correcting drift                   | ✓ VERIFIED | `scheduler.py:127` passes `base_time=task.next_run_at` to `calculate_next_run`; `:214-228` future-clamp loop advances stale base_time to next FUTURE occurrence, bounded by `_MAX_CLAMP_ITERATIONS` (`:16`). Behavioral check: far-past base_time returns datetime > now (DRIFT CLAMP OK). Test `test_next_run_at_advances_without_drift_across_ticks` passes. |
| 3   | (SC3/CRON-03) Optional `scheduler_jitter_seconds` knob (default 0) available to avoid thundering-herd                 | ✓ VERIFIED | Standalone `config.py:38` default 0 + env `SQLERY_SCHEDULER_JITTER_SECONDS` parsed as float (`:93,:104`); Django `settings.py:16` `SCHEDULER_JITTER_SECONDS: 0`. Scheduler `:132-134` applies bounded `random.uniform(0, jitter)` before enqueue, never fed into next_run_at. Mode-aware key lookup `_get_jitter_seconds` `:178-190`. Env-float check (JITTER FLOAT OK) and `test_scheduler_jitter_seconds_respected` pass. |
| 4   | (SC4/CRON-04) "Already queued" idempotency holds under brief two-leader overlap — cron fires exactly once            | ✓ VERIFIED | CAS-on-observed-`next_run_at` is the idempotency token. Standalone Postgres `with_for_update()` read-compare-write `:1026-1050`; SQLite predicate-CAS `rowcount == 1` `:1052-1071`. Django `.update(...)` rowcount-CAS `:691-696`. Scheduler `:137-144` returns None on lost CAS (no second enqueue). Tests `test_cron_fires_exactly_once_under_simulated_overlap` + `_under_threaded_overlap` + `test_two_attempts_single_fire` pass on SQLite. |
| 5   | (PLAN 03) interval and once schedule types still work and are not regressed                                          | ✓ VERIFIED | `scheduler.py:152-176`: non-cron branch preserves check-then-act, interval re-advance (`:163-168`), once disable (`:169-170`). Test `test_interval_and_once_not_regressed` passes; full unit suite 425 passed, 0 failures. |
| 6   | (PLAN 03) per-task try/except resilience in run_due_tasks preserved                                                  | ✓ VERIFIED | `scheduler.py:61-69` retains per-task `try/except ... continue` loop unchanged. |
| 7   | (PLAN 04) standalone SQLAlchemy advance path has DB-correctness coverage                                             | ✓ VERIFIED | `tests/test_core_standalone.py:200` `TestStandaloneAdvanceScheduledTask`: winning-CAS, stale-observed, two-attempt-single-fire — all pass directly against `SQLAlchemyBackend.advance_scheduled_task_if_due`. |
| 8   | (PLAN 01) `advance_scheduled_task_if_due` ABC abstractmethod with exact signature, implemented by BOTH backends      | ✓ VERIFIED | ABC `compat/__init__.py:541` `__isabstractmethod__` True, params `(self, task_id, observed_next_run_at, new_next_run_at, job_kwargs)`. Both `SQLAlchemyBackend` and `DjangoBackend` instantiate (no remaining abstractmethods). |

**Score:** 8/8 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `src/sqlery/compat/__init__.py` | ABC `advance_scheduled_task_if_due` abstractmethod | ✓ VERIFIED | `:541`, abstract, exact 5-param signature, full docstring referencing CRON-01/04 |
| `src/sqlery/fastapi_sqlery/backend.py` | Standalone impl (PG `with_for_update`, SQLite rowcount-CAS) | ✓ VERIFIED | `:987-1071` + helper `_build_queued_job` `:1073`; dual-dialect, `synchronize_session=False`, `rowcount == 1`; WIRED via scheduler |
| `src/sqlery/django_sqlery/backend.py` | Django impl (`transaction.atomic` + rowcount-CAS) | ✓ VERIFIED | `:659-697`, `transaction.atomic`, filter `next_run_at=observed_next_run_at`, `create_job` in-txn; WIRED |
| `src/sqlery/core/scheduler.py` | Hardened firing path: atomic advance, drift, jitter | ✓ VERIFIED | `:113-150` cron branch; `:127` drift base_time; `:132-134` jitter; old check-then-act commented out `:114-124`; WIRED by daemon + worker |
| `src/sqlery/fastapi_sqlery/config.py` | `scheduler_jitter_seconds` default 0 + env-float | ✓ VERIFIED | `:38,:93,:104` |
| `src/sqlery/django_sqlery/settings.py` | `SCHEDULER_JITTER_SECONDS` default 0 | ✓ VERIFIED | `:16` |
| `tests/test_atomic_scheduler.py` | Django CRON-01..04 behavioral tests | ✓ VERIFIED | `TestCronSemanticsHardening` `:369` — 5 tests, run on SQLite, pass |
| `tests/test_core_standalone.py` | Standalone advance correctness tests | ✓ VERIFIED | `TestStandaloneAdvanceScheduledTask` `:200` — pass |
| `tests/test_scheduler_drift_jitter.py` | Drift/jitter focused tests | ✓ VERIFIED | 10 test functions present, pass |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `scheduler._enqueue_for_scheduled_task` | `backend.advance_scheduled_task_if_due` | single atomic call | ✓ WIRED | `scheduler.py:137` |
| `scheduler.calculate_next_run` (cron) | `task.next_run_at` | `base_time=task.next_run_at` | ✓ WIRED | `scheduler.py:127` |
| scheduler jitter | `get_config` jitter key | mode-aware lookup + bounded delay | ✓ WIRED | `scheduler.py:185-186,132-134` |
| `daemon.py` | `Scheduler.run_due_tasks` | shared firing path | ✓ WIRED | `daemon.py:354,433` |
| `worker.py` (Phase 9 elected scheduler) | `Scheduler.run_due_tasks` | shared firing path | ✓ WIRED | `worker.py:506,571` |
| standalone CAS | `ScheduledTask.next_run_at == observed` | `with_for_update` / predicate-CAS | ✓ WIRED | `backend.py:1026,1056` |
| django CAS | `.filter(next_run_at=observed).update(...)` | rowcount-CAS in `transaction.atomic` | ✓ WIRED | `backend.py:690-696` |

### Data-Flow Trace (Level 4)

Backend methods return real persisted job instances (`session.refresh(job)` standalone; `create_job` return Django) — not static/empty. The advance writes a real DB UPDATE and a real QueuedJob row; tests assert `QueuedJob` count == 1 against the live DB. No hollow data paths.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| ABC is abstractmethod with exact signature | python introspection | params correct, abstract | ✓ PASS |
| Standalone backend instantiable | `SQLAlchemyBackend()` | STANDALONE INSTANTIABLE | ✓ PASS |
| Django backend instantiable | `DjangoBackend()` | DJANGO INSTANTIABLE | ✓ PASS |
| Drift future-clamp | far-past base_time | result > now (DRIFT CLAMP OK) | ✓ PASS |
| Jitter env parses as float | `SQLERY_SCHEDULER_JITTER_SECONDS=2.5` | 2.5 float (JITTER FLOAT OK) | ✓ PASS |
| Phase 10 test files | `pytest test_atomic_scheduler test_scheduler_drift_jitter test_core_standalone -q` | 27 passed, 4 skipped | ✓ PASS |
| Regression (unit suite) | `pytest tests/unit -q` | 425 passed, 10 skipped, 3 xfailed | ✓ PASS |

4 skipped tests are the pre-existing Postgres-only concurrency tests (`@skip_on_sqlite`) that auto-skip without `SQLERY_TEST_PG_URL` — documented and expected.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| CRON-01 | 10-01, 10-03, 10-04 | Atomic enqueue + next_run_at advance in one txn (both backends) | ✓ SATISFIED | Truth 1; both backend impls atomic; tests pass |
| CRON-02 | 10-03, 10-04 | Next occurrence from scheduled time, drift correction | ✓ SATISFIED | Truth 2; `base_time=task.next_run_at` + future-clamp; tests pass |
| CRON-03 | 10-02, 10-03, 10-04 | Optional `scheduler_jitter_seconds` knob (default 0) | ✓ SATISFIED | Truth 3; config in both modes + bounded delay; tests pass |
| CRON-04 | 10-01, 10-03, 10-04 | Idempotency under two-leader overlap, exactly-once | ✓ SATISFIED | Truth 4; CAS-on-observed token; overlap tests pass on SQLite |

All 4 requirement IDs from plan frontmatter are accounted for in REQUIREMENTS.md (lines 34-37) and mapped to Phase 10 (lines 75-78). No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| — | — | None | — | No TBD/FIXME/XXX debt markers; no TODO/HACK/placeholder in new methods; no stub returns; no inline imports in new code |

### Human Verification Required

None. The phase goal (exactly-once / no-drift / no-skip cron under overlap and crash) is proven by automated DB-correctness tests on SQLite — the CAS design makes exactly-once engine-independent. The Postgres concurrency lane and full cross-matrix parity proof are explicitly scoped to Phase 11 (out of scope here).

### Gaps Summary

No gaps. All 8 must-have truths verified, all artifacts substantive and wired, all 4 CRON requirements satisfied, no anti-patterns. Both the daemon and the Phase 9 worker-elected scheduler inherit the hardened shared path with no caller-side changes.

**Noted deviation (acceptable, not a gap):** Plan 04 instructed testing via the `sqlery.executor.TaskExecutor` alias. The executor correctly identified this resolves to the LEGACY unhardened Django executor and instead tested `sqlery.core.scheduler.Scheduler` — the actual runtime firing path used by daemon (`daemon.py:354`) and worker (`worker.py:506`). This deviation strengthens verification: the legacy `TaskExecutor` was intentionally NOT hardened, and testing it would NOT prove CRON-01..04. Verified that the tested path is the runtime path.

---

_Verified: 2026-06-08_
_Verifier: Claude (gsd-verifier)_
