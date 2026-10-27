### `t/integration/test_group_chains.py`

Portable idea: a pipeline of stages where each stage is a batch of jobs that run in parallel, and each stage feeds the next (sqlery: batches linked by dependencies). Requires a result store (Redis in Celery); skipped otherwise.

**Results flow between batches**
- Stage 1: 3 independent jobs, each summing 3 ones. Stages 2-4: 3 jobs each, each receiving the previous stage's results. Final job sums everything. Run the worker; expect: final result 3^5 = 243, within 30s.
- Same pipeline, repeated with each combination of: old/new message protocol, pipeline serialized to JSON and rebuilt before enqueue, job IDs pre-assigned before enqueue, and the whole pipeline attached as the follow-up of a 2-job parent batch. Expect: same result in every combination.
- Same pipeline; expect: each of the 12 jobs executed exactly once (12 execution records, none duplicated).

**Failure inside a batch**
- Stage with 3 jobs where the last raises, and that job has an error handler attached (failure at the first stage, or at the third). Expect: error handler runs once; the failed job is recorded as failed with the original exception type preserved.
- Same scenario; expect: the 2 sibling jobs in the failing stage still complete, earlier stages are fully done (3 each), later stages never run (0 executions), and the queue then goes idle (no stuck or stray jobs).

**Scale and payload size**
- 10 stages of 25 jobs each (250 jobs) plus a final sum. Expect: correct result (25^11) within 60s, each job executed exactly once (250 unique executions).
- Same large pipeline; expect: only the first stage (25 jobs) is published at enqueue time, and no enqueued job payload exceeds 128 KB (payload size does not grow with pipeline depth or width).
