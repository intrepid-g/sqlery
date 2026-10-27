### `t/smoke/tests/test_rpc_backend.py`

**Result round-trip**
- Enqueue one job (identity function) with results stored in the same channel the worker uses (no separate result store); wait for the result; expect: the result equals the job's input.

**Polling completed jobs must not leave residue (regression #4830)**
- Enqueue 3 jobs (add i+i), poll each one's status until it is finished, with a bounded retry window (about 1 second between tries, up to the result timeout); expect: each ends as success with result i+i.
- After a job is finished, poll its status 5 more times; expect: still success every time, served from what is already known (cached).
- After all those repeated polls; expect: the broker holds 0 ready messages (nothing re-queued or recirculating), checked with a short retry window to allow for settling.
- Polling must consume (acknowledge) the final result, never put it back on the queue.

**Worker setup under test**
- Worker runs with concurrency 1 and prefetch 1; expect the above to hold under that minimal setup.

**Portable note**
- The RPC/AMQP/RabbitMQ wiring is Celery-specific; the portable guarantees are: result stored and retrievable, status polling is idempotent and side-effect free, and no leftover messages/rows after completion.
