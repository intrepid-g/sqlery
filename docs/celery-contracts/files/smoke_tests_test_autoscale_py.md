### `t/smoke/tests/test_autoscale.py`

**Autoscaling worker pool with keepalive (min 1, max 2 processes, keepalive 5s)**
- Start a worker that may grow from 1 to 2 pool processes. Enqueue 2 long-running jobs at once; expect: both done successfully, and the pool has grown to exactly 2 worker processes.
- Repeat the same burst of 2 jobs 3 more times, with short idle gaps (shorter than the keepalive) between bursts, even when the total elapsed time exceeds the keepalive measured from the first growth; expect: every burst completes with both jobs done, and the pool still consists of the exact same 2 processes (none retired, none replaced).
- Idle time is measured per gap, not from the moment the pool first grew: continued activity keeps surplus processes alive, so processes are not churned (no needless shrink-then-regrow) under steady bursty load.
- The worker's pool statistics are queryable while running, reporting the set of live pool processes, so an operator can verify pool stability.
