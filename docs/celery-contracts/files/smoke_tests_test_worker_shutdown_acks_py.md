### `t/smoke/tests/test_worker_shutdown_acks.py`

**Graceful shutdown with late acknowledgement (worker concurrency 2, one job reserved at a time)**
- Enqueue a long job (420s) and a short job (3s); start the worker; ask it to shut down gracefully (SIGTERM) while both run; expect: the worker logs a warm shutdown, the short job finishes and its result is retrievable.
- Same scenario; expect: the short job's completion is acknowledged to the queue immediately while the drain is still in progress (not deferred until the worker has fully stopped); only the long job remains in-flight/unacknowledged.
- Same scenario, then hard-kill the worker before the long job completes; expect: only the long job is returned to the queue for redelivery (exactly 1 job), the job that finished during the drain is NOT redelivered or re-run.

**Long drain outlasting the connection heartbeat (late ack, concurrency 1, heartbeat 2s)**
- Enqueue one 10s job, start it, request graceful shutdown; expect: the job completes and its result is retrievable even though the drain takes far longer than the heartbeat interval.
- Same scenario; expect: the job is acknowledged, not requeued: queue ends with 0 total, 0 ready, 0 in-flight (the job does not run a second time).
- Same scenario; expect: no "missed heartbeats" warnings, i.e. the worker keeps its connection to the queue alive while draining so the queue never considers it dead and releases its unacked jobs.

**Portable guarantees for sqlery (summary)**
- A job that finishes during shutdown must be marked done immediately, so a later crash cannot cause it to be re-run.
- Only jobs genuinely in progress at a hard kill are eligible for recovery.
- A worker draining a long job must keep its heartbeat/lease alive so it is never declared dead and its job never reclaimed or duplicated.
