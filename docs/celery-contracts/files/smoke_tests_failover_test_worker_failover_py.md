### `t/smoke/tests/failover/test_worker_failover.py`

**Setup:** two workers on separate queues, both configured to acknowledge a job only after it finishes (late ack), so a job in flight on a dead worker is not lost.

**Worker failure**
- Two workers are running. Kill the first worker abruptly, then enqueue a long-running job on that worker's queue; expect: the job still completes successfully and its result is retrievable within the timeout (the surviving worker or system picks it up).

**Recovery**
- Two workers are running. Kill all of them, restart the first worker, then enqueue a long-running job on its queue; expect: the restarted worker reconnects to the queue and completes the job, result retrievable within the timeout.
