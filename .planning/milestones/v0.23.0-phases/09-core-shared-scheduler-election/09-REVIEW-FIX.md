---
phase: 09-core-shared-scheduler-election
fixed_at: 2026-06-08T00:00:00Z
review_path: .planning/phases/09-core-shared-scheduler-election/09-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 1
skipped: 1
status: partial
---

# Phase 09: Code Review Fix Report

**Fixed at:** 2026-06-08
**Source review:** .planning/phases/09-core-shared-scheduler-election/09-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope (critical_warning): 2 (WR-01, WR-02)
- Fixed: 1
- Skipped: 1

## Fixed Issues

### WR-01: Scheduler lease is not renewed while the worker is busy executing a job — leadership flaps on any job longer than the TTL

**Files modified:** `src/sqlery/core/worker.py`, `tests/unit/test_worker.py`
**Commit:** c704f38
**Applied fix:** The main loop only renewed held scheduler leases at the top of
each iteration, but `_fork_and_execute` blocks in the `waitpid` poll loop for the
full job duration (up to `timeout + 60s`). With TTL = `poll_interval * 3` (~15s
default), any longer job let the worker's lease expire while it was alive and
healthy, causing scheduler-leadership flapping (single-worker: cron stalls;
multi-worker: two-leader overlap).

Changes:
- Added instance attributes `self._owned_queues: set[str]` and
  `self._lease_secs: int` in `WorkerProcess.__init__` so `_fork_and_execute`
  can see the held queues and TTL.
- In `run()`, mirrored the local `lease_secs` and `owned_queues` onto those
  instance attributes after the initial election claim (the in-place `|=`
  update in the election step keeps the shared set object in sync).
- In the `_fork_and_execute` child-wait loop — alongside the existing
  `self._check_heartbeat()` poll — added a guarded `renew_queue_leases` call
  that refreshes the held leases each 0.5s tick. The renewal is wrapped in
  try/except (catch, log a warning, continue) so a renew error never aborts
  the wait or crashes job execution, per CLAUDE.md error-handling conventions.
- Per CLAUDE.md, no working lines were deleted; only new lines were added (the
  prior renew-at-top-of-loop logic is preserved and still runs each cycle).

Added `test_leases_renewed_while_blocking_on_long_job` to `TestForkLifecycle`:
it seeds a held lease with a PAST `expires_at` (simulating a long job that has
already outlived the TTL), drives `_fork_and_execute` with a mocked fork whose
`waitpid` reports "not yet exited" for several poll iterations before exit, and
asserts the lease's `expires_at` was pushed back into the future during the
blocking wait. Full suite: `32 passed` (`uv run --active pytest
tests/unit/test_worker.py -q`).

**Note:** This fix touches lease-renewal timing/logic. The behavioral test
proves renewal occurs during a blocking job, but the developer should confirm
the in-wait renewal cadence is appropriate for their deployment TTLs.

## Skipped Issues

### WR-02: Two-leader cron double-fire under lease overlap (known / deferred to Phase 10)

**File:** `src/sqlery/core/worker.py:540-560`
**Reason:** Explicitly DEFERRED to Phase 10's idempotency guard (CRON-04) per the
phase plan and the review brief. WR-02 requires atomic `next_run` advance and
idempotency under two-leader overlap, which is out of scope for Phase 09. Not a
blocker. Note: fixing WR-01 materially narrows the window in which this overlap
can occur during normal operation, reducing interim exposure to this deferred
risk.
**Original issue:** `renew_queue_leases` returns `None` and the worker never
checks whether renewal actually preserved ownership; if another node took over
the lease, `owned_queues` still contains the queue and the worker keeps firing
`run_due_tasks`, allowing a due `ScheduledTask` to be enqueued twice (the current
`has_pending_job_for_scheduled_task` de-dupe is best-effort, not atomic against
concurrent leaders).

---

_Fixed: 2026-06-08_
_Fixer: gsd-code-fixer_
_Iteration: 1_
