### `t/smoke/tests/test_consumer.py`

Portable guarantees: worker survives losing its connection to the store, does not over-fetch while recovering, and resumes normal work afterwards.

**Over-fetch protection after the database connection drops**
- Worker allows up to 10 claimed-but-unfinished jobs (concurrency 5, claim-ahead 2). Enqueue N long jobs (N = 1..5), all running, then drop and restore the DB connection; expect: the worker temporarily lowers its claim-ahead limit to max(2, 10 - N*2) and logs why (N jobs currently running).
- Same scenario; expect: the worker logs that the limit will be restored gradually to 10 as running jobs finish (not instantly).
- Enqueue 20 short jobs, drop and restore the connection; expect: once they complete, the worker logs that normal operation resumed and claim-ahead is back at the maximum of 10.
- Backend whose semantics do not allow adjusting the limit: expect the worker skips the reduction and logs that it skipped it (no crash, no partial adjustment).
- Feature switched off (reduction disabled, claim-ahead 1, 5 long jobs running): drop and restore the connection, enqueue a quick job; expect it does NOT run until a long job frees a slot, then it runs and completes (no over-claiming while busy).

**Running jobs when the connection is lost**
- Option "cancel long-running jobs on connection loss" on, late-acknowledge on, 5 long jobs running: drop and restore the connection, enqueue a quick job; expect the worker still picks up and runs the quick job (cancelled/abandoned jobs free their slots, worker does not wedge).

**Worker recovers after the store goes down**
- Run one job successfully (result None). Kill the database; expect the worker keeps retrying the connection with growing back-off (e.g. 8s on 4th of up to 100 attempts) and logs each attempt, rather than exiting.
- Restore the database; enqueue a group of 5 jobs; expect all 5 complete with results [None]*5 (worker resumed consuming without a restart).
- After recovery, enqueue a chain of two groups of 5 independent jobs (second stage depends on first); expect all complete and the final stage returns 5 results (dependencies still honoured after an outage).

**Test-suite caveat**
- Prefetch-reduction tests are marked expected-flaky on Redis-like backends; the guarantee still applies but timing of the log lines is not deterministic there.
