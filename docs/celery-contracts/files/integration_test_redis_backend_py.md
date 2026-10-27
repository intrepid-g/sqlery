### `t/integration/test_redis_backend.py`

Portable guarantee: results are stored and retrievable, expiry is honoured per outcome, concurrent waiters never corrupt shared state, dependency groups (chord) run their final step exactly once with every part recorded once, and nothing is leaked after cleanup. Redis wiring itself (pub/sub, decode_responses) is Celery-specific.

**Result storage and expiry**
- Store a structured result (nested values, text) for a job, then fetch it with a 5s timeout; expect: the identical value back.
- Configure a storage mode that cannot hold compressed payloads while compression is requested; expect: compression silently skipped for stored results, global config unchanged, result still readable.
- Store one successful and one failed result with success retention 60s and failure retention 600s; expect: success expires within 60s, failure lives longer than 60s but at most 600s.
- Store a result then forget it; expect: it is gone (cleanup always runs, even on test failure).

**Concurrent waiting on results**
- Enqueue 12 jobs, then wait on each result from its own thread (30s timeout); expect: no thread errors, each thread gets exactly its own job's value.
- Many threads repeatedly subscribe/unsubscribe to job results while another thread keeps polling for events; expect: no errors, only the original subscription remains, and the server holds no leftover per-job channels.
- While a subscribe/unsubscribe is mid-flight, a garbage-collection finalizer cancels another result subscription on the same thread; expect: no deadlock within 30s, no errors, the nested cancel waits until the outer call returns (never interleaves), all pending cancels completed, only the original subscription remains, no leaked channels.

**Dependency groups (header jobs then one final body job)**
- Group of jobs whose final step has retention 600s vs normal 60s; expect: all group bookkeeping entries (count, parts, size, done markers) get the longer retention, and the group's job list is restorable and equal to the original.
- Group of 3 where header job 0 reports twice with different values (e.g. redelivered after a worker was lost, late-ack); expect: recorded once, first value kept, count is 1, and the final step does not run early or receive a duplicate. Verify both with ordered and unordered result collection.
- A record written by an older-version worker without the dedupe marker is already present; expect: the group is not stalled, the remaining part completes, final step runs once with results [1, 2, 3] in header order.
- After the final step completes; expect: all group bookkeeping is removed from the store (no residue).
