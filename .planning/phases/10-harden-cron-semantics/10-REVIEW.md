---
phase: 10-harden-cron-semantics
reviewed: 2026-06-08T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/compat/__init__.py
  - src/sqlery/core/scheduler.py
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/fastapi_sqlery/backend.py
  - src/sqlery/django_sqlery/settings.py
  - src/sqlery/fastapi_sqlery/config.py
findings:
  critical: 1
  warning: 6
  info: 3
  total: 10
status: issues_found
---

# Phase 10: Code Review Report

**Reviewed:** 2026-06-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Reviewed the Phase 10 cron-hardening changes against diff base `d8e5113`: the new
`advance_scheduled_task_if_due` ABC method and its Django + standalone implementations,
the scheduler's atomic-advance call path, the drift-corrected `calculate_next_run`
future-clamp loop, and the jitter config wiring in both modes.

**Core atomicity verdict (the central concern) holds.** The CAS-plus-enqueue is genuinely
atomic on both backends:

- **Standalone** runs the advance and the job `add()` inside one `get_session()` (single
  transaction; verified `get_session` yields a session that only commits on explicit
  `session.commit()`). Postgres uses a blocking `with_for_update()` re-read-under-lock
  (correct for a single-key row — `skip_locked` would have been wrong here). SQLite uses a
  predicate-CAS `update(...).where(next_run_at == observed)` with
  `synchronize_session=False` and `rowcount == 1`, with an explicit `rollback()` on the
  lost branch. The lost-CAS path creates no job. No TOCTOU.
- **Django** wraps the rowcount-CAS `.update()` and `create_job()` in one
  `transaction.atomic()`. Lost CAS (`advanced != 1`) returns before any job is created.

The advance/enqueue truly commit-or-roll-back together, so a crash between them cannot
double-fire. A crash **before** commit re-evaluates the same tick next cycle (acceptable —
this is at-least-once-with-CAS-dedup, the intended tradeoff). tz-aware/naive normalization
in the Postgres CAS predicate is handled; the Django predicate compares ORM-native values
on both sides so it is internally consistent.

One BLOCKER remains around what happens when the future-clamp loop hits its cap, plus six
warnings (a backend parity divergence, a latent default mismatch, a stale second scheduler
path, jitter-sleep-before-CAS semantics, and two robustness gaps). Cross-matrix CI parity
proof is explicitly deferred to Phase 11 and is NOT flagged.

## Critical Issues

### CR-01: Clamp-cap exhaustion returns a past `next_run_at`, causing an immediate re-fire busy-loop

**File:** `src/sqlery/core/scheduler.py:218-228`
**Issue:** When the future-clamp loop reaches `_MAX_CLAMP_ITERATIONS` with `candidate <= now`,
the method logs a warning and **returns the past candidate anyway**. That past value becomes
`new_next_run`, which is written into `next_run_at` by the atomic advance. On the very next
scheduler cycle the task is still `next_run_at <= now`, so it is selected as due again,
fires again, and re-enters the same clamp loop — a tight enqueue-every-cycle loop that will
hammer the queue and the DB indefinitely for that task. The bound prevents an infinite loop
*inside one call*, but it does not prevent an unbounded firing loop *across* calls.

In practice the cap (~3.8 years of minute-ticks) is only reached for a genuinely pathological
or unsatisfiable cron after very long downtime, but the failure mode is a runaway producer,
not a benign skip. The CAS dedup does not help here: each cycle the row's `next_run_at` still
equals the observed value, so the advance keeps winning.

**Fix:** When the cap is hit and `candidate <= now`, advance `next_run_at` to a *future*
sentinel and/or disable the task instead of persisting a past time. For example, clamp the
base forward to `now` before the final occurrence, or fall back to "next occurrence strictly
after now" computed from `now` rather than from the stale `candidate`:

```python
if iterations >= _MAX_CLAMP_ITERATIONS and candidate <= now:
    logger.warning(
        f"calculate_next_run hit clamp cap ({_MAX_CLAMP_ITERATIONS}) for "
        f"'{cron_expression}'; recomputing from now to avoid re-fire loop"
    )
    # Recompute strictly from current time so the persisted next_run_at is in
    # the future and the task does not immediately re-qualify as due.
    candidate = next_cron_occurrence(cron_expression, now)
return candidate
```

(Per the project's edit rules, comment out the old `return candidate` line rather than
deleting it.)

## Warnings

### WR-01: `_build_queued_job` defaults `retry_backoff` to 0.0 while the model/`create_job` default is 1.0

**File:** `src/sqlery/fastapi_sqlery/backend.py:1086`
**Issue:** `_build_queued_job` uses `job_kwargs.get("retry_backoff", 0.0)`, but the model
field default (`src/sqlery/core/models.py:83`) and `DjangoBackend.create_job` both treat the
backoff baseline as `1.0`. For the cron path this is latent because the scheduler always
passes `retry_backoff` (`scheduler.py:102`, defaulting to `1.0`). But `_build_queued_job` is
a general "mirror of create_job" helper; any caller that omits the key gets a silently
different (and broken — `1.0 * 2**n` vs `0.0 * 2**n = 0`) retry-backoff curve than the Django
backend produces. This is a cross-backend parity defect waiting to surface.
**Fix:** Match the canonical default: `retry_backoff=job_kwargs.get("retry_backoff", 1.0)`.

### WR-02: `_build_queued_job` does not set `version`, diverging from SQLite CAS expectations

**File:** `src/sqlery/fastapi_sqlery/backend.py:1079-1104`
**Issue:** `QueuedJob.version` (models.py:114) backs the SQLite optimistic-locking CAS used by
`claim_job`/`atomic_claim_job`. `_build_queued_job` never sets it, relying on the model
default of `0`. `create_job` also relies on the default, so behavior is currently equivalent —
but the helper's stated contract is "mirrors create_job's field mapping," and it explicitly
enumerates nearly every other field. Omitting `version` silently couples correctness to the
model default staying `0`. If the default ever changes, jobs enqueued via the cron path would
start with a different version than the claim CAS expects.
**Fix:** Either add `version=0` explicitly for parity, or add a comment documenting the
intentional reliance on the model default.

### WR-03: Jitter `time.sleep` runs before the atomic advance, inside the per-task loop

**File:** `src/sqlery/core/scheduler.py:132-139`
**Issue:** `time.sleep(random.uniform(0, jitter))` is executed synchronously *before*
`advance_scheduled_task_if_due`, inside `run_due_tasks`'s per-task loop. With jitter enabled
and N due cron tasks in one cycle, the daemon thread blocks for up to `N * jitter` seconds
serially, delaying every subsequent due task (cron and non-cron) and the whole daemon cycle.
The CAS correctly prevents double-fire after the sleep, so this is not a correctness bug, but
it is a real availability/timeliness regression for multi-task deployments that enable jitter.
The docstring frames the sleep-then-CAS ordering as deliberate (a crash during sleep just
re-evaluates next cycle), which is fine; the serial-blocking cost is the concern.
**Fix:** Apply jitter per-task without serializing the loop (e.g. compute a jittered
`scheduled_at` on the enqueued job instead of sleeping, or cap aggregate sleep), or document
that jitter is intended only for single-cron-per-cycle deployments. Performance is out of v1
review scope, but the cross-task delay is a behavioral surprise worth recording.

### WR-04: Second scheduler path (`scheduler_tasks.py`) still uses the old non-atomic claim and was not hardened

**File:** `src/sqlery/core/scheduler.py:73-150` (changed) vs `src/sqlery/core/scheduler_tasks.py:43,58` (unchanged)
**Issue:** Phase 10 hardened `Scheduler._enqueue_for_scheduled_task` to the atomic CAS, but a
parallel code path in `scheduler_tasks.py` still calls `backend.claim_due_scheduled_task(...)`
(the prior `SELECT ... FOR UPDATE SKIP LOCKED` claim, which the Django backend implements via
`atomic_claim_job_queryset` and SQLite cannot lock). If both paths are reachable in any
deployment, the exactly-once guarantee the phase advertises is only partial — the un-migrated
path retains the old race window. This file is out of the stated diff scope, so flagging as a
warning rather than blocker, but it should be confirmed dead or migrated.
**Fix:** Confirm whether `scheduler_tasks.py` is live. If live, route it through
`advance_scheduled_task_if_due`; if dead, remove it so reviewers don't assume two divergent
firing semantics coexist.

### WR-05: `advance_scheduled_task_if_due` does not re-check `enabled` in the CAS predicate

**File:** `src/sqlery/django_sqlery/backend.py:690-697`, `src/sqlery/fastapi_sqlery/backend.py:1019-1071`
**Issue:** The CAS filters only on `id` + `next_run_at == observed`. `get_due_scheduled_tasks`
selects `enabled=True`, but there is a TOCTOU window between that read and the advance: an
operator (or a `once` task disabling itself) could set `enabled=False` after the task is
collected as due. The advance still wins the CAS on the unchanged `next_run_at` and enqueues a
job for a now-disabled task. The prior `claim_due_scheduled_task` path guarded `enabled=True`
in its predicate; this new primitive dropped that guard.
**Fix:** Add `enabled=True` (Django: `.filter(id=..., next_run_at=..., enabled=True)`;
standalone Postgres branch: include `existing.enabled` in the compare; SQLite branch: add
`.where(ScheduledTask.enabled == True)`), so a task disabled mid-cycle does not fire.

### WR-06: SQLite/standalone Postgres tz normalization relies on `next_run_at` never being None, but the column is nullable

**File:** `src/sqlery/fastapi_sqlery/backend.py:1031-1035`
**Issue:** `existing.next_run_at.tzinfo` is dereferenced without a None check. `next_run_at`
is `datetime | None` (models.py:41; Django field `null=True`). A task can legitimately have
`next_run_at = None` (e.g. a `once` task after firing, set via
`update_scheduled_task(..., next_run_at=None)` in `scheduler.py:170`). `get_due_scheduled_tasks`
filters `next_run_at <= now` so a None row should not normally be selected — but a concurrent
`once`-disable between the due-scan and this locked re-read would yield `existing.next_run_at
is None`, raising `AttributeError` inside the transaction. The Django rowcount-CAS path is
immune (a SQL equality against a NULL column simply matches zero rows), so this is a
standalone-only crash and a backend parity gap.
**Fix:** Guard for None before the `.tzinfo` access and treat None as a lost CAS:
```python
if existing is None or existing.next_run_at is None:
    return None
```

## Info

### IN-01: Misleading comment — SQLite does not return naive datetimes here, the column type does

**File:** `src/sqlery/fastapi_sqlery/backend.py:1030,1058`, also `:339,387`
**Issue:** Repeated comment "SQLite returns naive datetimes; normalize before compare." The
normalization is correct and harmless, but the framing obscures that the same code runs for
the Postgres `skip_locked` branch where values are already aware. Minor doc clarity only.
**Fix:** Reword to "DB column may be naive (SQLite); normalize before compare."

### IN-02: `_MAX_CLAMP_ITERATIONS` magic-number rationale is sound but the cap is effectively unbounded wall-clock cost

**File:** `src/sqlery/core/scheduler.py:16,220`
**Issue:** 2,000,000 iterations of `next_cron_occurrence` (each parses the cron string anew —
`parse_cron_string` is called every call, crontab.py:147) is a multi-second-to-minutes CPU
spin in the pathological case, blocking the daemon. Correctness is bounded; cost is not.
Pre-parsing the crontab once and iterating its generator would bound both. (Performance is
out of v1 scope; noted for awareness alongside CR-01 which shares the root cause.)
**Fix:** Consider parsing once and walking `Crontab.date_times(base)` rather than re-calling
`next_cron_occurrence` per iteration.

### IN-03: Jitter config key casing diverges by mode and is easy to misconfigure silently

**File:** `src/sqlery/core/scheduler.py:185`, `src/sqlery/django_sqlery/settings.py:16`, `src/sqlery/fastapi_sqlery/config.py:38`
**Issue:** Django reads `SCHEDULER_JITTER_SECONDS` (upper-snake), standalone reads
`scheduler_jitter_seconds` (lower). `_get_jitter_seconds` branches on `is_django_mode()` to
pick the key. This works, but a Django operator who sets `scheduler_jitter_seconds` (or vice
versa) gets silent default-0 with no warning. The float-coercion fallback also swallows
`ValueError`/`TypeError` to 0.0 silently.
**Fix:** Acceptable as-is given the per-mode config convention; optionally log at debug when a
non-numeric jitter value is coerced to 0.0 so misconfiguration is diagnosable.

---

_Reviewed: 2026-06-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
