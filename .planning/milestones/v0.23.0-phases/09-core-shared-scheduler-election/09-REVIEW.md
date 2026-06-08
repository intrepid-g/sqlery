---
phase: 09-core-shared-scheduler-election
reviewed: 2026-06-08T00:00:00Z
depth: standard
files_reviewed: 2
files_reviewed_list:
  - src/sqlery/core/worker.py
  - tests/unit/test_worker.py
findings:
  critical: 0
  warning: 1
  info: 2
  total: 3
status: issues_found
---

# Phase 9: Code Review Report

**Reviewed:** 2026-06-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 2
**Status:** issues_found

## Summary

Iteration-2 re-review focused on confirming the WR-01 fix (scheduler leases
renewed during long blocking jobs) and checking for regressions. WR-02
(two-leader cron double-fire) is intentionally deferred to Phase 10 (CRON-04
idempotency guard) and is NOT re-flagged.

**WR-01 is genuinely resolved.** Verified by direct code trace and the new
`test_leases_renewed_while_blocking_on_long_job` test (all 32 tests in
`test_worker.py` pass):

- **Renewal actually happens during blocking execution.** Lines 748-754 run
  inside the `while not child_exited:` wait loop in `_fork_and_execute`, after
  each `time.sleep(0.5)` poll. The test drives 3 "not-yet-exited" `waitpid`
  polls followed by an exit, starts the lease with `expires_at` in the past,
  and asserts the lease is pushed back into the future — proving the in-wait
  renewal executes at least once.
- **Error-isolated.** The renew is wrapped in `try/except` (lines 748-754) that
  logs a warning and continues; a renew failure can never abort the
  `waitpid` wait or crash job execution.
- **Job-claim path unchanged.** `claim_job(self.queues, ...)` at line 577 still
  passes the full `self.queues`. The renewal touches only `self._owned_queues`,
  which gates cron-firing, not job claiming (ELECT-07 preserved; confirmed by
  `test_job_claim_path_uses_full_queue_set_regardless_of_leases`).
- **State stays in sync.** `self._owned_queues` is bound to the same set object
  as `run()`'s local `owned_queues` at line 526; line 566 mutates it in place
  with `|=` (set `__ior__`), so additions made by the main loop are visible to
  `_fork_and_execute`. No rebinding occurs that would desync the two views.

**No regressions** were introduced by the fix. The new instance attributes
(`_owned_queues`, `_lease_secs`) are initialized to safe empty/zero values in
`__init__` (lines 435-436); if `_fork_and_execute` ran before `run()` populated
them, the `if self._owned_queues:` guard would short-circuit and skip the renew.
Signature `renew_queue_leases(owned_queues, daemon_id, lease_secs)` at lines
750-751 matches the ABC and both concrete backends.

## Warnings

### WR-01: In-wait renewal can resurrect a lease already lost to another leader

**File:** `src/sqlery/core/worker.py:748-754`
**Issue:** The in-wait renewal calls `renew_queue_leases(...)` with no guard on
whether the lease is still actually held. In the real backends, renew is an
`UPDATE ... WHERE queue_name IN (...) AND daemon_id = self.worker_id` with **no
`expires_at` predicate** (`fastapi_sqlery/backend.py:470-477`; the Django
backend is equivalent). Consider a worker whose process is paused (GC/STW, swap,
SIGSTOP) longer than the lease TTL (`poll_interval * 3`) while its child runs:
the lease expires, a second worker re-claims `default` via `claim_queue_leases`
(which DOES check expiry), and `daemon_id` flips to the new owner. When the
first worker resumes, its in-wait renew matches `daemon_id = old_worker` and
finds no row (correct no-op). **But** if the lease expired and was NOT yet
re-claimed by anyone, the renew silently extends the stale lease, re-asserting
leadership the worker had effectively forfeited. In that window two workers can
believe they lead the same queue. This is the same failure class as the
deferred WR-02 (double-fire) and is bounded by the Phase-10 CRON-04 idempotency
guard, so it is correctly NOT a blocker — but it should be tracked alongside
WR-02 rather than considered fully closed by this fix, because the in-wait
renewal slightly widens the leadership-overlap window relative to top-of-loop
renewal only.
**Fix:** When CRON-04 lands, the idempotency guard covers the double-fire. In
the interim, have `renew_queue_leases` report which queues it actually renewed
so `_fork_and_execute` can drop a queue from `self._owned_queues` when its renew
fails to match:
```python
try:
    if self._owned_queues:
        renewed = self.backend.renew_queue_leases(
            sorted(self._owned_queues), self.worker_id, self._lease_secs
        )
        # if renew returns which queues it touched, prune the rest:
        # if renewed is not None:
        #     self._owned_queues &= set(renewed)
except Exception as e:
    logger.warning(f"Lease renew during job execution failed: {e}")
```

## Info

### IN-01: In-wait renewal fires every 0.5s, far more often than the TTL needs

**File:** `src/sqlery/core/worker.py:740-754`
**Issue:** The renewal runs on every `waitpid` poll (every 0.5s) for the entire
job duration. With a lease TTL of `poll_interval * 3` (≈15s by default), the
worker issues an `UPDATE` roughly 30x more often than necessary to keep the
lease alive. This is a correctness no-op (renewing early is harmless) but adds
avoidable write load during long jobs. Performance tuning is out of v1 review
scope; noting only because the cadence is incidental rather than intentional.
**Fix:** Gate the renew on elapsed time, e.g. renew only when
`time.monotonic() - last_renew > self._lease_secs / 3`, tracking `last_renew`
across loop iterations.

### IN-02: `_owned_queues` / `owned_queues` aliasing relies on in-place `|=`

**File:** `src/sqlery/core/worker.py:526,566,749`
**Issue:** The sync between `run()`'s local `owned_queues` and the instance
`self._owned_queues` depends on the subtle invariant that line 566 uses `|=`
(in-place `set.__ior__`) and that `owned_queues` is never rebound after line
526. If a future edit changes line 566 to `owned_queues = owned_queues | newly`
(rebinding to a new object) or reassigns `owned_queues` inside the loop, the
instance view would silently desync and the in-wait renewal would renew a stale
queue set — a hard-to-spot regression with no test guarding the aliasing.
**Fix:** Make the relationship explicit by re-syncing after any mutation, e.g.
`owned_queues |= newly_claimed; self._owned_queues = owned_queues` at line 566,
or drop the local entirely and operate on `self._owned_queues` throughout `run()`.

---

_Reviewed: 2026-06-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
