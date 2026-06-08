---
phase: 10-harden-cron-semantics
reviewed: 2026-06-08T00:00:00Z
depth: standard
iteration: 2
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/compat/__init__.py
  - src/sqlery/core/scheduler.py
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/fastapi_sqlery/backend.py
  - src/sqlery/django_sqlery/settings.py
  - src/sqlery/fastapi_sqlery/config.py
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 10: Code Review Report (Iteration 2)

**Reviewed:** 2026-06-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Re-review of the Phase 10 cron-hardening fixes applied since iteration 1 (1 BLOCKER +
6 Warning + 3 Info). I re-traced each claimed fix in source and verified no regression
was introduced, with focused attention on the two callouts: the SQLite
`.where(enabled == True)` predicate and the `calculate_next_run` clamp-recompute path.

**All four claimed fixes are genuinely resolved:**

- **CR-01 (BLOCKER) — RESOLVED.** `calculate_next_run` (scheduler.py:223-238) now recomputes
  `next_cron_occurrence(cron_expression, now)` when the clamp cap is reached and
  `candidate <= now`. Confirmed against `crontab.py:156-158`: `next_cron_occurrence`
  advances `base + 1 minute` then takes the next match, so the recomputed value is strictly
  `> now`. The persisted `next_run_at` is therefore always in the future and the task can no
  longer re-qualify as due every cycle. The runaway producer is closed. The old `return`
  path is commented out, not deleted (per edit rules).
- **WR-05 (enabled re-check) — RESOLVED in all three paths.** Django filter adds
  `enabled=True` (backend.py:697); standalone Postgres branch adds `if not existing.enabled:
  return None` (backend.py:1038); SQLite branch adds `.where(ScheduledTask.enabled == True)`
  (backend.py:1069). The SQLite predicate is correct: `enabled` is a boolean column (0/1 in
  SQLite) and the literal `True` compiles to `1`; it composes cleanly with the existing
  `synchronize_session=False` raw-SQL CAS without re-introducing the ORM evaluator. No
  regression.
- **WR-06 (nullable `next_run_at`) — RESOLVED.** Postgres branch guards
  `existing is None or existing.next_run_at is None` (backend.py:1034) before the `.tzinfo`
  dereference. The SQLite branch never touches `.tzinfo` (it compares in SQL), so it remains
  immune. Backend parity restored for this crash mode.
- **WR-01 (`retry_backoff` default) — RESOLVED.** `_build_queued_job` now defaults to `1.0`
  (backend.py:1102), matching the model field (`models.py:83`) and `create_job`. Old `0.0`
  default commented out.

**Regression checks performed (no new defects found):**

- SQLite CAS tz consistency: `observed_next_run_at` originates from `task.next_run_at` read
  back from SQLite (naive), and `new_next_run_at` (tz-aware) is written and read back naive
  next cycle; the `next_run_at == observed` comparison stays naive==naive. Internally
  consistent; the `enabled` predicate change does not perturb it.
- Clamp recompute strictness: verified `> now` for any satisfiable cron (see CR-01 above).
- Django CAS: `.filter(id, next_run_at=observed, enabled=True).update(...)` plus
  `create_job` remain inside one `transaction.atomic()`; lost CAS (`advanced != 1`) returns
  before any job is created. Adding `enabled=True` only narrows the predicate (matches zero
  rows for a disabled task), which is the intended behavior — no false negatives for enabled
  tasks since `get_due_scheduled_tasks` already filters `enabled=True`.

Remaining open items are the two deferred warnings (WR-03 perf-scope jitter serialization,
WR-04 legacy `scheduler_tasks.py` path) and three info items carried forward. Per the
iteration-2 mandate, WR-04 (legacy path) and the Phase 11 cross-matrix CI parity proof are
NOT flagged as blockers. No BLOCKER remains.

## Warnings

### WR-03: Jitter `time.sleep` runs serially before the atomic advance, inside the per-task loop

**File:** `src/sqlery/core/scheduler.py:132-134`
**Issue:** `time.sleep(random.uniform(0, jitter))` executes synchronously before
`advance_scheduled_task_if_due` inside `run_due_tasks`'s per-task loop. With jitter enabled
and N due cron tasks in one cycle, the daemon thread blocks up to `N * jitter` seconds
serially, delaying every subsequent due task and the whole daemon cycle. Correctness is
unaffected (the CAS still prevents double-fire after the sleep); this is a timeliness
regression for multi-task deployments that enable jitter. Carried forward from iteration 1;
deferred as out of v1 perf scope but recorded as a behavioral surprise.
**Fix:** Apply jitter without serializing the loop (e.g. compute a jittered `scheduled_at`
on the enqueued job instead of sleeping the daemon thread), or document that jitter targets
single-cron-per-cycle deployments.

### WR-04: Second scheduler path (`scheduler_tasks.py`) still uses the old non-atomic claim

**File:** `src/sqlery/core/scheduler.py` (hardened) vs `src/sqlery/core/scheduler_tasks.py` (unchanged)
**Issue:** `scheduler_tasks.py` still calls `backend.claim_due_scheduled_task(...)` (the prior
`SELECT ... FOR UPDATE SKIP LOCKED` claim) rather than the new atomic
`advance_scheduled_task_if_due` primitive. If that path is reachable in any deployment, the
exactly-once guarantee Phase 10 advertises is only partial there. Per the iteration-2
mandate this is explicitly NOT a blocker (distinct legacy path, out of this phase's scope).
Recorded so reviewers do not assume a single firing semantics.
**Fix:** Confirm whether `scheduler_tasks.py` is live. If live, route it through
`advance_scheduled_task_if_due`; if dead, remove it. (Out of Phase 10 scope.)

## Info

### IN-01: Comment framing — "SQLite returns naive datetimes" attributes naivety to the engine, not the column

**File:** `src/sqlery/fastapi_sqlery/backend.py:1040` ("DB column may be naive (SQLite)…")
**Issue:** The reworded comment is improved over iteration 1 ("DB column may be naive") and
is now accurate. Noted only because the same normalization branch also runs under the
Postgres `with_for_update` path where values are already aware; the `if existing.next_run_at.tzinfo`
guard handles that correctly. No action required.
**Fix:** None needed; comment is acceptable.

### IN-02: `_MAX_CLAMP_ITERATIONS` bounds correctness but not wall-clock cost

**File:** `src/sqlery/core/scheduler.py:16,220-238`
**Issue:** Up to 2,000,000 calls to `next_cron_occurrence` (each re-parses the cron string via
`parse_cron_string`, crontab.py:147) is a multi-second-to-minutes CPU spin in the
pathological long-downtime case, blocking the daemon. CR-01's recompute does not change this
inner loop cost. Correctness is now bounded; cost is not. Out of v1 perf scope.
**Fix:** Consider parsing the crontab once and walking its occurrence generator from `base`
rather than re-calling `next_cron_occurrence` per iteration.

### IN-03: Jitter config key casing diverges by mode; misconfiguration is silent

**File:** `src/sqlery/core/scheduler.py:185-190`, `src/sqlery/django_sqlery/settings.py:16`, `src/sqlery/fastapi_sqlery/config.py:38`
**Issue:** Django reads `SCHEDULER_JITTER_SECONDS` (upper-snake), standalone reads
`scheduler_jitter_seconds` (lower); `_get_jitter_seconds` branches on `is_django_mode()`. An
operator who sets the wrong-cased key gets a silent default-0. The float coercion also
swallows `TypeError`/`ValueError` to `0.0` silently (scheduler.py:189). Acceptable given the
per-mode config convention.
**Fix:** Optionally log at debug when a non-numeric jitter value is coerced to `0.0` so
misconfiguration is diagnosable.

---

_Reviewed: 2026-06-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Iteration: 2_
