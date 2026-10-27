### `t/unit/worker/test_eta_query_task.py`

**Scheduled (future-dated) jobs stay visible to inspection**
- Schedule a job to run in 10 seconds; query the worker for it; expect: reported as "scheduled", and not yet counted as reserved or active.
- Schedule a job, then let its run time arrive and the worker pick it up; expect: it is reported as "reserved", is no longer in the scheduled set, and the worker is notified of the new request exactly once.
- Schedule a job, run it to completion; expect: querying it returns nothing, and it is gone from the overall request registry, the scheduled set and the reserved set.

**Lost connection to the queue**
- Schedule a job, then the worker loses its queue connection before the run time; expect: the pending job is purged (query returns nothing, removed from registry and scheduled set), since the queue will redeliver it.
- A job whose run time was already past may start before the scheduler finishes registering it as scheduled (race between timer and registering thread); even if it ends up in both scheduled and active sets, a connection loss must not drop it; expect: it stays in the registry, still reported "active", and remains inspectable and revocable.

**Revoking a pending scheduled job**
- Schedule a job, revoke it with terminate while it is still waiting; expect: the job is findable by id, the revocation is recorded, termination is deferred (nothing running to kill yet), and when its time arrives it refuses to run (raises a revoked error).
