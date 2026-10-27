### `t/smoke/tests/stamping/test_revoke.py`

**Revoke by stamp, after enqueue (chain of two jobs, second waits a few seconds)**
- Enqueue a 2-step dependent chain (job 1 passes a value to job 2; job 2 sleeps for a timeout, then logs "Done waiting"). Right after enqueue, revoke every job carrying a given stamp (with terminate). Expect: job 2 never reaches "Done waiting" within the timeout (it is cancelled or killed mid-run), job 2 does not complete.

**Revoke by stamp, before enqueue (standing revocation)**
- Register a revocation for a stamp value on the worker first, then enqueue the same chain. Expect: the worker discards the stamped job on arrival and logs "Discarding revoked task" with the reason "revoked by header: <stamp>". The job never runs.

**Portable guarantees implied**
- Revocation matches jobs by metadata (stamp/header), not just by job id.
- A revocation is remembered by the worker and applies to jobs enqueued later (not only to jobs already queued).
- Revocation with terminate stops a job that is already running.
- Works across mixed worker versions (old and latest worker in the same cluster).
