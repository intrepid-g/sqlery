### `t/smoke/tests/test_revoke.py`

Setup: two workers share the queue system; only one of them runs a given job. Job start is tracked (a "started" state is visible). The cancel request reaches every worker.

**Cancel a running job (no terminate)**
- Enqueue a 5s job, wait until it is started, request cancel (no terminate); expect: job keeps running, state sequence is exactly started -> success, never "revoked".
- Same job: the client that requested the cancel still gets the job's real result (true).
- Cancel is broadcast to all workers, but only the worker running the job matters; the other workers must not mark the job revoked.

**Cancel with terminate**
- Enqueue a 30s job, wait until started, cancel with terminate; expect: job is killed, final state is revoked, success is never seen in the state history.
- Fetching the result of the terminated job raises a "job revoked" error.

**Cancel a job that has not started**
- Enqueue a job delayed 30s, cancel it before it is due; expect: final state revoked, states seen only pending then revoked (never started or success).
- Fetching its result raises a "job revoked" error.

**Client polling / timeouts**
- Polling the state reaches a terminal state within the result timeout; a job that never starts or never finishes fails with a clear message listing states seen.
