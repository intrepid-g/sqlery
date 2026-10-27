### `t/smoke/tests/quorum_queues/test_cycle_detection.py`

Context: a broker (RabbitMQ <4.0.1 with quorum queues) could wrongly drop or reject a message that is re-published to the same queue (a cycle). Portable guarantee: retrying a job must never make it vanish or get stuck.

**Delayed retry**
- Enqueue a job with a 3-second start delay that fails once and asks to retry itself with the same 3-second delay; expect: it runs again after the delay, finishes successfully on the second attempt, and the result (1 retry used) is retrievable within the timeout.

**Multiple retries**
- Enqueue a job (no start delay) that fails and retries itself twice, each retry delayed by 3 seconds; expect: the job is not lost or rejected by the queue on either re-queue, finishes after the third run, and the result (2 retries used) is retrievable within the timeout.

**Implied guarantees**
- A job re-queued to the same queue it came from (retry) must always be accepted and executed; no loop/duplicate detection may swallow it.
- Retry count is tracked accurately across re-queues.
