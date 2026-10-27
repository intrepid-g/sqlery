### `t/smoke/tests/test_query_task.py`

**Inspecting a job that is waiting for its run time**
- Enqueue a job with a 10-second delay (run-at in the future) on a specific worker's queue; while it is still waiting, ask the worker "where is job X?"; expect: the job is found and reported as "scheduled", not missing (the old bug: only found once running).
- In the same check, list the worker's scheduled (delayed) jobs; expect: job X appears in that list too, so both views agree.
- Query a single named worker only; expect: the answer returns promptly rather than waiting for every worker to reply.

**Timing and races**
- The delay must be long enough that the job is still waiting when the status query arrives; a job that already ran and left worker state cannot be found by retrying. Both checks run in one polling pass so they cannot race each other past the run time.
- Poll the status query repeatedly until a deadline; expect: it is eventually seen as scheduled, otherwise fail with a clear message ("never found the task while it was waiting").

**Completion**
- After the delay elapses, the job runs normally; expect: result is retrievable within the timeout and correct (add 2+2 gives 4), i.e. being inspected does not disturb or lose the job.
