### `t/unit/concurrency/test_thread.py`

Thread-pool execution mode (worker runs jobs on a pool of threads).

**Basic execution**
- Start a thread pool, submit one job (2 + 2) with success and error callbacks; expect: the job runs without error and the pool stops cleanly.
- Ask a running pool for its info; expect: a non-empty description of the pool (e.g. concurrency/size) is available.

**Shutdown**
- Stop an idle pool, then try to submit a job; expect: submission is rejected with an error (no job runs on a stopped pool).
- Pool with 1 thread: job 1 is running (blocked), job 2 is queued behind it; stop the pool; expect: job 2 is cancelled and never runs, while the stop waits for the running job 1 to finish.
- After job 1 is released, expect: stop completes within a bounded time, no thread is leaked.
- Call stop twice on the same pool; expect: second call is harmless (stop is idempotent).
- Even if a test or caller fails midway, expect: the running job can be released and the pool still stopped (no leaked threads).
