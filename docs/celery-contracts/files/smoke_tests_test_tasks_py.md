### `t/smoke/tests/test_tasks.py`

**Enqueue options**
- Job type defaults to "discard result". Enqueue job A by name (no options), then enqueue the same job with empty options, then job B by name; expect: the empty-options enqueue does not leak or cache the "discard result" default, so A and B both still return their results (3 and 11).

**Worker crash isolation (concurrency 1)**
- Run a job that kills its own execution process with SIGKILL; expect: caller gets a "worker lost" error, and the pool spawns a new child process (a pid not seen before) while the pool size returns to normal.
- Run a job that calls hard exit (exit code 1); expect: the same "worker lost" error and a respawned child.
- Run a job that sleeps past its hard time limit; expect: a "time limit exceeded" error and a respawned child.
- (Memory exhaustion is a known scenario, skipped only because it destabilises CI.)

**Failure is logged accurately**
- Job killed by SIGKILL; expect the worker log to say it exited prematurely with signal 9, and the error surfaced to the caller to carry that reason.
- Job hard-exits with code 1; expect a log line "exited prematurely: exitcode 1".
- Job exceeds hard limit (2s); expect a log line naming the limit and the job, and an error recording the limit.

**Retry when the worker is lost**
- With late acknowledgement, reject-on-worker-lost and retry-on-worker-lost on: a job whose process is always SIGKILLed is received 3 times, retried exactly 2 times, then ends failed with "worker lost"; expect: retries bounded, final failure reported, no infinite loop.

**Replacement and chaining across queues**
- Two workers on two different queues. A job replaces itself with another job routed to the second queue, chained into a follow-up job (add 2) also on the second queue; expect: final result 42 (40 + 2), the replacement and chain honour their own queue routing.
- Sanity: the two workers listen on distinct queues.

**Time limits**
- Soft limit lower than hard limit, job overruns; expect: job gets a catchable "soft time limit exceeded" error and the caller sees it as the failure.
- Soft limit greater than hard limit; expect: enqueue is rejected immediately with an error ("soft limit must be less than or equal to hard limit"); nothing is queued.
- After a hard timeout kills a job, enqueue a trivial job; expect: it runs and returns normally (worker still functional).
