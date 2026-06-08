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
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 09: Code Review Report

**Reviewed:** 2026-06-08
**Depth:** standard
**Files Reviewed:** 2
**Status:** issues_found

## Summary

Phase 09 wires the daemon's per-queue lease scheduler-election lifecycle into
`WorkerProcess.run` (`src/sqlery/core/worker.py`). The wiring is faithful to the
reference analog in `src/sqlery/core/daemon.py`:

- The initial claim is wrapped in `try/except` with an `owned_queues = set()`
  fallback, so an election failure logs and the worker still starts and claims
  jobs (ELECT-07 satisfied).
- The per-cycle election step (renew held leases, re-claim unowned/expired,
  fire cron only for held queues) is wrapped in its own `try/except` so an
  election error logs and the loop continues — election never crashes the
  worker loop (verified at worker.py:538-564).
- The job-claim path is `claim_job(self.queues, self.worker_id)` (worker.py:566)
  using the full configured queue set, NOT scoped to `owned_queues` — confirmed
  unchanged and correctly verified by `test_job_claim_path_uses_full_queue_set_regardless_of_leases`
  (ELECT-07).
- Lease TTL is `self.poll_interval * 3` (worker.py:504), mirroring the daemon's
  `check_interval * 3`.
- Held leases are released on graceful shutdown via the `finally:` block
  (worker.py:646-649), and `owned_queues` is guaranteed bound before the
  main-loop `try/finally` (set in both branches of the initial-claim
  try/except at worker.py:506/515).
- Backend signatures match the call sites:
  `claim_queue_leases(queues, daemon_id, node_id, pid, lease_secs)`,
  `renew_queue_leases(owned_queues, daemon_id, lease_secs)`,
  `release_queue_leases(owned_queues, daemon_id)` (compat/__init__.py:118-169).
  Passing `worker_id` as the `daemon_id` slot is correct.
- `run_due_tasks(queue_names=owned_queues)` accepts a `set` — `run_due_tasks`
  only does membership testing (`t.queue_name in queue_names`, scheduler.py:49),
  and the daemon passes the same set type, so no type mismatch.

The test suite is behavioral, not mock-only: assertions read real `fake_backend`
state (`_leases`, `_jobs`) produced by the actual `run()` election path, and the
FakeBackend lease implementation (conftest.py:620-647) correctly models
skip-live-foreign / take-over-expired semantics. The wiring cannot pass these
tests unless it actually runs.

Two robustness defects were found. The most significant (WR-01) is a real
divergence from the daemon model that causes scheduler-leadership flapping
during normal long-job execution — distinct from the cron-double-fire item the
prompt deferred to Phase 10.

## Warnings

### WR-01: Scheduler lease is not renewed while the worker is busy executing a job — leadership flaps on any job longer than the TTL

**File:** `src/sqlery/core/worker.py:540-542` (renew site), `src/sqlery/core/worker.py:602-613` and `654-731` (`_fork_and_execute` blocking)

**Issue:** Lease renewal (`renew_queue_leases`) only runs at the top of each
main-loop iteration (worker.py:539-542). When a job is claimed, the worker calls
`_fork_and_execute`, which blocks in the `waitpid` loop for the full job
duration (up to `timeout + 60s`, worker.py:689-731) before the loop iterates
again. During that window no lease renewal occurs.

The lease TTL is `poll_interval * 3` (default `5 * 3 = 15s`). Any job that runs
longer than ~15s therefore lets this worker's scheduler lease **expire while the
worker is alive and healthy**. Consequences:

- Single-worker deployment: scheduler leadership is lost mid-job; cron stops
  firing until the job finishes and a later idle cycle re-claims the expired
  lease. Due cron tasks are silently delayed for the job's duration.
- Multi-worker deployment: a second worker observes the expired lease, takes
  over scheduling, and now two live workers believe they lead the queue
  (two-leader overlap), amplifying the deferred cron-double-fire risk below.

This is a genuine divergence from the daemon reference, NOT the deferred
cron-semantics item: the daemon (`daemon.py:404-419`) renews leases every loop
iteration because jobs run in **separate worker subprocesses** spawned by
`WorkerPoolManager` — the daemon loop never blocks on job execution. The bare
worker runs cron election and job execution in the same process and the same
loop iteration, so a long job starves lease renewal. The TTL was sized for the
daemon's tight, never-blocking loop, not for a loop that blocks for
`timeout + 60s`.

**Fix:** Renew held leases from inside the blocking wait, alongside the existing
heartbeat poll. `_fork_and_execute` already calls `self._check_heartbeat()`
every 0.5s in the wait loop (worker.py:730); renew there too (guarded so an
error never aborts the wait), or have `_check_heartbeat` carry the renewal.
Concretely, in the `_fork_and_execute` wait loop:

```python
            # Sleep briefly so parent stays responsive to signals
            time.sleep(0.5)
            self._check_heartbeat()
            # Keep scheduler leadership alive across long jobs — without this
            # the lease (poll_interval*3) expires mid-job and another worker
            # takes over scheduling (two-leader overlap).
            try:
                if self._owned_queues:
                    self.backend.renew_queue_leases(
                        sorted(self._owned_queues), self.worker_id, self._lease_secs
                    )
            except Exception as e:
                logger.warning(f"Lease renew during job execution failed: {e}")
```

This requires promoting `owned_queues` and `lease_secs` to instance attributes
(`self._owned_queues`, `self._lease_secs`) so `_fork_and_execute` can see them.
Alternatively, size `lease_secs` against the worst-case job duration
(`max(poll_interval * 3, DEFAULT_TIMEOUT_SECONDS + 60)`), but in-loop renewal is
the correct fix and matches the daemon's "renew continuously" intent.

### WR-02: Two-leader cron double-fire under lease overlap (known / deferred to Phase 10)

**File:** `src/sqlery/core/worker.py:540-560`

**Issue:** `renew_queue_leases` returns `None` and the worker never checks
whether the renewal actually preserved ownership (worker.py:540-542). If this
worker's lease was taken over by another node (e.g. after the WR-01 expiry
window, or clock skew), `owned_queues` still contains the queue and the worker
keeps firing `run_due_tasks(queue_names=owned_queues)` (worker.py:560). With two
processes simultaneously believing they lead the same queue, a due `ScheduledTask`
can be enqueued twice. The current de-dupe in `_enqueue_for_scheduled_task`
(`has_pending_job_for_scheduled_task`, scheduler.py:78) is a best-effort check,
not atomic against concurrent leaders.

**Fix:** Deferred to Phase 10 per the phase plan (atomic next_run advance, drift,
idempotency under two-leader overlap). Flagged here as a **known/deferred item,
not a blocker**, per the review brief. Note that WR-01 materially widens the
window in which this overlap can occur during normal operation, so fixing WR-01
reduces exposure to this deferred risk in the interim.

## Info

### IN-01: `renew_queue_leases` return value ignored — no ownership-loss detection

**File:** `src/sqlery/core/worker.py:540-542`

**Issue:** The worker assumes renewal always succeeds. The ABC declares
`renew_queue_leases -> None`, so there is currently no way to detect a lost
lease even if the worker wanted to. This is consistent with the daemon
reference and acceptable for Phase 09, but the lack of a success signal is the
root enabler of WR-02.

**Fix:** Consider (in Phase 10) having `renew_queue_leases` return the subset of
queues whose lease was actually still owned and renewed, and dropping any
non-renewed queue from `owned_queues` so the worker stops scheduling it.

### IN-02: Debug-artifact log line `logger.info(".")`

**File:** `src/sqlery/core/worker.py:570`

**Issue:** `logger.info(".")` on every idle poll emits a noise log line with no
context. Pre-existing (not introduced by Phase 09) but sits directly in the
election/poll path touched by this phase.

**Fix:** Remove the line or downgrade to `logger.debug` with a meaningful
message, e.g. `logger.debug(f"Worker {self.worker_id} idle, no job claimed")`.

### IN-03: Stale comment cross-reference to a non-existent line

**File:** `src/sqlery/core/worker.py:617`

**Issue:** The comment "will ... heartbeat idle when entering the poll sleep
(line 418)" references "line 418", which does not correspond to the heartbeat
call after the Phase 09 insertions shifted line numbers. Misleading for future
readers.

**Fix:** Replace the hard-coded line reference with a symbolic description,
e.g. "when entering the bounded poll-sleep below".

---

_Reviewed: 2026-06-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
