### `t/unit/concurrency/test_gevent.py`

**Startup / environment**
- Start a worker configured for the cooperative (green-thread) execution mode; expect: the runtime is monkey-patched for cooperative I/O before anything else runs.

**Timed callbacks (scheduler timer)**
- Schedule a callback to run later; expect: it is tracked as pending.
- A scheduled callback fires or exits; expect: it is cancelled/killed and removed from the pending set (no leak).
- Clear the timer with several pending callbacks; expect: all are cancelled and the pending set is empty.
- Clear the timer when cancelling one callback raises an error; expect: the error is swallowed and clearing still completes.
- Cancel a timer entry that was already cancelled; expect: no error.

**Concurrency pool: lifecycle**
- Start the pool, submit one job, stop the pool; expect: job accepted, clean stop.
- Stop a pool that was never started (no underlying pool); expect: no error.

**Concurrency pool: resizing**
- Grow a pool of 1 by 1; expect: limit 2, capacity 2, one waiting slot released so a waiting job can start.
- Grow by 4; expect: limit 5, and 4 waiting slots released.
- Shrink a pool of 5 by 3; expect: limit 2, capacity 2, free slots reduced accordingly (never over-promised).
- Shrink a pool of 2 by 1; expect: limit 1, free slot count consistent.
- Set the limit directly to 3; expect: the pool reports 3 worker slots.
- Pool has only 1 running job but capacity 5; expect: reported worker count is capacity (5), not running jobs, so autoscaling decisions use capacity.
- Autoscaler with capacity 3, max 10, and 5 reserved jobs waiting; expect: it grows by exactly 2 (from capacity, not from currently running jobs).

**Cancelling a running job**
- Submit a job, then terminate it by id; expect: it was tracked with a completion hook, and the running job is killed.
- Job finishes (any outcome); expect: its entry is removed from the running-job map (no leak); removing an already-empty entry is safe.
- A job is killed mid-run (exit signal raised inside it); expect: the result is reported as a non-success "no result" outcome (False, None, None), not a crash.
- A job that completes normally; expect: its return value is passed through unchanged.

**Timeouts**
- Run a job with a 10s time limit that finishes in time; expect: the limit is applied (10s), the job runs, and the success callback fires.
- Run a job that exceeds its time limit; expect: the timeout callback is called with (failure, limit=10), the worker itself survives.
