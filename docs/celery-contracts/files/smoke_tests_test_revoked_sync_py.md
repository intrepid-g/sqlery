### `t/smoke/tests/test_revoked_sync.py`

**Cancelled-job list shared between workers (hello / startup sync)**
- Another worker hands over its list of cancelled job ids, stamped with a clock far ahead of ours; expect: ids are re-stamped with the receiving worker's own clock, whatever stamps they came with.
- The cancelled-id list is full (tiny capacity) after receiving the handover; then a new job is cancelled; expect: the new cancellation is kept, not purged immediately (so the job does not run).
- A job with a delay/ETA is enqueued, the worker holds it, then it is cancelled; expect: the job is discarded with a "discarding cancelled job" log, never runs, no success log.
- Reply to a handover request; expect: it contains only the list of cancelled ids, and includes every id that was handed over.

**Restart sync between workers (including mixed versions)**
- Cancel job A on worker 1, restart worker 2; expect: worker 2 now knows A is cancelled (it takes its neighbour's list on startup).
- Cancel job B on worker 2, restart worker 1; expect: worker 1 knows B, and still knows A (the id it had before restart, which the other worker retained).
- Old release and current source run side by side; expect: each accepts the other's list format on sync.
- Eventual consistency: checks retry for up to 60 s before failing, tolerating propagation delay.
