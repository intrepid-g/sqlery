### `t/smoke/tests/test_disabled_backend.py`

**Results disabled (no result storage)**
- Run the whole system (client and worker) with result storage turned off; expect: jobs still enqueue and execute, and nothing in the flow requires reading a stored result.
- Asking for a result when storage is disabled; expect: a clear "not supported" error, never a silent wrong answer (so callers must observe outcomes through side effects or callbacks).

**Failure in a dependency chain**
- Enqueue a chain: job 1 (raises), job 2 (depends on job 1); expect: job 1 failed, job 2 never runs.
- Same chain with an error callback attached; expect: the error callback runs exactly once, even though no job state can be looked up (regression: failing the chain must not crash by trying to read state from the disabled store).
- Marking the dependents of a failed job as failed must work without querying stored state; expect: no NotImplementedError, no worker crash, callback still delivered.

**Observing outcomes without a result store**
- Error callback writes a marker to an external place; poll it until a deadline; expect: marker appears exactly once with the expected payload ("failure_handled"), within the timeout.
- Clean up the marker afterwards, even if the assertion fails.
