---
phase: 10-harden-cron-semantics
plan: 03
subsystem: scheduler
tags: [cron, atomicity, drift, jitter, CRON-01, CRON-02, CRON-03, CRON-04]
requires:
  - "backend.advance_scheduled_task_if_due (Plan 01)"
  - "config key scheduler_jitter_seconds / SCHEDULER_JITTER_SECONDS (Plan 02)"
  - "crontab.next_cron_occurrence (unchanged)"
provides:
  - "Hardened cron firing path: atomic advance+enqueue (one CAS), drift correction, bounded optional jitter"
  - "Scheduler.calculate_next_run future-clamp loop (CRON-02)"
  - "Scheduler._get_jitter_seconds mode-aware jitter resolution helper"
affects:
  - "src/sqlery/core/daemon.py (inherits hardened firing, no caller-side change)"
  - "src/sqlery/core/worker.py (Phase 9 worker-elected scheduler, inherits behavior)"
tech-stack:
  added: []
  patterns:
    - "Atomic advance==enqueue idempotency token replaces check-then-act on the cron path"
    - "Drift correction by computing next occurrence from task.next_run_at, not wall-clock now"
    - "Bounded future-clamp loop with a max-iteration cap (no missed-tick replay)"
    - "Config-only bounded jitter (random.uniform(0, jitter)) applied before enqueue, never fed into next_run_at"
    - "Mode-aware config key lookup (is_django_mode) so operator jitter overrides take effect in both modes"
key-files:
  created:
    - "tests/test_scheduler_drift_jitter.py"
  modified:
    - "src/sqlery/core/scheduler.py"
decisions:
  - "Jitter applied ONLY on the cron enqueue path (interval/once have no atomic-advance primitive this phase and keep prior behavior)"
  - "Jitter sleep happens BEFORE the atomic advance so a crash mid-sleep simply re-evaluates the tick next cycle (no partial state) — matches threat T-10-06 accept rationale"
  - "Mode-aware jitter key via is_django_mode() (Plan 02 option 1) so Django UPPER_SNAKE and standalone lowercase operator overrides both take effect"
  - "Future-clamp cap = 2,000,000 iterations (~3.8y of every-minute downtime) with a warning log if hit, rather than spinning"
  - "Non-cron (interval/once) branches keep the has_pending_job_for_scheduled_task dedup gate; only the cron path drops it in favor of the CAS"
metrics:
  duration: ~15m
  completed: 2026-06-08
  tasks: 2
  files: 2
---

# Phase 10 Plan 03: Hardened Cron Firing Path Summary

Reworked `src/sqlery/core/scheduler.py` so the shared cron firing path (used by both the daemon and the Phase 9 worker-elected scheduler) now advances `next_run_at` and enqueues through a single atomic `backend.advance_scheduled_task_if_due` call, computes the next occurrence from the scheduled time (drift-corrected, future-clamped), and applies an optional bounded operator-set jitter — delivering all four CRON requirements at one observable point. Interval/once scheduling and the per-task resilient loop are untouched.

## What Was Built

- **CRON-02 — drift correction + future clamp** (`calculate_next_run`): After computing the candidate via `next_cron_occurrence`, a bounded loop advances the candidate until it is strictly after `datetime.now(timezone.utc)`. This corrects per-cycle drift (next occurrence computed from the scheduled `base_time`, not wall-clock now) and, on long downtime, jumps to the next FUTURE occurrence instead of replaying every missed tick. The old single-shot `return next_cron_occurrence(...)` is commented out (CLAUDE.md: comment, don't delete). `base_time is None -> now` default and tz-normalization preserved for register/update callers.

- **CRON-01 + CRON-04 — atomic advance == enqueue** (`_enqueue_for_scheduled_task`, cron branch): The check-then-act sequence (`has_pending_job_for_scheduled_task` -> `create_job` -> `update_scheduled_task_next_run`) is commented out and replaced by a single `self.backend.advance_scheduled_task_if_due(task.id, observed_due, new_next_run, job_kwargs)` call. `observed_due = task.next_run_at` is captured before any advance and is the CAS token. When the call returns a job, it is returned; when it returns `None` (lost CAS / already fired) the method logs at info and returns `None` with NO fallback `create_job`. Two overlapping leaders therefore produce exactly one job.

- **CRON-03 — bounded optional jitter**: `_get_jitter_seconds()` resolves the jitter value, and when `> 0` a `time.sleep(random.uniform(0, jitter))` delay is applied BEFORE the advance call. The jitter is never fed into `calculate_next_run`.

- **New module-level imports** (top-level, no inline imports per CLAUDE.md): `random`, `time`, and `get_config`, `is_django_mode` from `..compat`. Added module constant `_MAX_CLAMP_ITERATIONS = 2_000_000`.

## job_kwargs Fields Passed to advance_scheduled_task_if_due

Built once at the top of `_enqueue_for_scheduled_task`, mirroring the prior `create_job` call site:

| Key | Source |
|---|---|
| `task_path` | `task.task_path` |
| `kwargs` | `task.get_kwargs_dict()` if present else `{}` |
| `queue_name` | `task.queue_name` |
| `priority` | `task.priority` |
| `scheduled_at` | `None` (run immediately) |
| `max_retries` | `getattr(task, "max_retries", 0)` |
| `retry_backoff` | `getattr(task, "retry_backoff", 1.0)` |
| `allow_parallel` | `getattr(task, "allow_parallel", False)` |
| `timeout_seconds` | `getattr(task, "timeout_seconds", None)` |
| `scheduled_task_id` | `task.id` |

This dict is reused as `create_job(**job_kwargs)` on the interval/once paths and passed positionally as the 4th arg to `advance_scheduled_task_if_due` on the cron path.

## Jitter Config Key Used

Mode-aware (Plan 02 option 1) in `_get_jitter_seconds`:

```python
jitter_key = "SCHEDULER_JITTER_SECONDS" if is_django_mode() else "scheduler_jitter_seconds"
value = get_config(jitter_key, 0)
```

Reasons: `get_config` does NO key transformation (per 10-02-SUMMARY), Django stores the UPPER_SNAKE key and standalone the lowercase key. The mode-aware lookup honors operator overrides in both modes; non-numeric/None/`<=0` is treated as disabled (`float()` guarded by try/except, returns `0.0`).

## Where Jitter Is Applied

ONLY on the cron enqueue path, BEFORE the atomic advance. interval/once paths apply no jitter (no atomic-advance primitive this phase). Placing the sleep before the advance means a crash during the sleep leaves no partial state — the tick is re-evaluated next cycle (threat T-10-06 accept rationale).

## Future-Clamp Cap Value

`_MAX_CLAMP_ITERATIONS = 2_000_000` (~3.8 years of every-minute downtime). If the cap is reached while the candidate is still `<= now`, a warning is logged and the last candidate is returned rather than spinning indefinitely (threat-safe termination for a misbehaving expression).

## Verification

- Task 1 inline verify printed `DRIFT CLAMP OK`: far-past base_time clamps to a future datetime; near-now base_time returns the correct next future occurrence.
- Task 2 inline verify printed `SCHEDULER WIRING OK`: `advance_scheduled_task_if_due`, `base_time=task.next_run_at`, `scheduler_jitter_seconds`, `random.uniform` all present; module-level `random` and `time` resolve.
- `tests/test_scheduler_drift_jitter.py` — 10 passed (4 drift-clamp + 6 atomic-advance/jitter/branch-preservation).
- Regression: `tests/test_scheduler_compat.py` 48 passed; `tests/test_atomic_scheduler.py` 6 passed, 4 skipped (SQLite concurrency skips, pre-existing).
- `import sqlery.core.daemon` + `import sqlery.core.scheduler` succeed (callers unaffected).
- `black --check` on `scheduler.py` reports only PRE-EXISTING single-quote reformatting in untouched code; `black --diff` confirms NONE of this plan's added lines are flagged (see Deviations).

## Deviations from Plan

### Auto-fixed / Decisions

**1. [Rule 3 - Blocking] `black --check src/sqlery/core/scheduler.py` fails on pre-existing single-quote style — out of scope**
- **Found during:** Task 1 verification (`<verification>` requires `black --check` to pass).
- **Issue:** The pristine file uses single quotes throughout (black wants double quotes) and would reformat ~30 untouched lines. These failures are NOT caused by this plan.
- **Fix/Decision:** Did NOT reformat the whole file (out of scope per executor scope boundary; conflicts with the user's global no-blanket-line-replacement rule). I DID format my own added lines to be black-clean: collapsed two of my new statements (`new_next_run = ...` and `jitter_key = ...`) to the single-line form black prefers. `black --diff` confirms none of this plan's added lines are flagged. This mirrors Plan 10-02's identical, documented deviation.
- **Files modified:** none beyond the planned scheduler edits.

**2. [Test setup] Fixed over-mocked interval/once tests**
- **Found during:** Task 2 GREEN.
- **Issue:** The interval/once tests in the RED test file relied on a default `MagicMock` for `has_pending_job_for_scheduled_task`, which returns a truthy mock — causing the new non-cron dedup gate to early-return `None`.
- **Fix:** Set `has_pending_job_for_scheduled_task.return_value = False` in those two tests so they exercise the create_job + advance branches. Committed together with the Task 2 implementation (same TDD cycle).

## Threat Surface

No new security surface. Per the plan threat register: T-10-05 (double-fire) mitigated by the single atomic CAS; T-10-06 (skipped tick) accepted via atomic advance+enqueue with jitter-sleep-before-advance; T-10-07 (covert delay channel) mitigated by config-only bounded `random.uniform(0, jitter)` never fed into next_run_at; T-10-SC — no new packages (`random`/`time` are stdlib).

## Self-Check: PASSED

- FOUND: src/sqlery/core/scheduler.py (advance_scheduled_task_if_due, base_time=task.next_run_at, _MAX_CLAMP_ITERATIONS, _get_jitter_seconds)
- FOUND: tests/test_scheduler_drift_jitter.py (10 tests)
- FOUND commit 5b1cd21 (RED test)
- FOUND commit adbf1ab (Task 1 GREEN)
- FOUND commit 87657fa (Task 2 GREEN)
