### `t/smoke/tests/test_concurrency_auto.py`

Contract: an "auto" worker concurrency sizes the process pool from the CPU budget the worker is actually allowed, not from the host. Tested with real containers and real CPU quotas.

**Auto sizing (process-based worker)**
- Start a worker with concurrency=auto on a 2-CPU quota; expect: pool size = min(available CPUs, 2), the startup log states the resolved size and the quota, and the reported max concurrency and number of worker processes both match.
- Start a worker with auto on a 0.5-CPU quota; expect: rounded up to 1 worker process, never 0.
- Start a worker with auto and no quota; expect: falls back to the available CPU count, and the log says no quota was found.
- Start a worker with auto, no quota, but pinned to one core; expect: pool size 1 (taken from the CPU affinity, not the host CPU count).

**Auto is a no-op where it does not apply**
- Start a thread-based worker with auto on a 2-CPU quota; expect: a log line saying auto only sizes the process pool, and concurrency is the normal default, not capped by the quota.
- Start a single-task (solo) worker with auto; expect: the same log line, no "resolved to" log, concurrency stays 1, quota not consulted.

**Regression guards**
- Start a worker without auto on a 1-CPU quota (host has more than 1 CPU); expect: no auto log, concurrency equals host CPU count, quota ignored (skipped on single-CPU hosts).
- Start a worker with an explicit concurrency of 3 under a quota; expect: exactly 3 processes, no auto log; an explicit number always wins over the quota.
