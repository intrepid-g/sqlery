### `t/smoke/tests/test_redis_backend_gevent.py`

Portable guarantee: a client running under cooperative concurrency (green threads, process-wide monkey patching) must be able to wait on job results without hanging, and its timeout must always be honoured.

**Concurrent result waiting**
- Enqueue 20 jobs (add i+i), then wait on all 20 results at the same time from 20 concurrent green threads, each with a timeout; expect: every wait returns, no hang, and results are [0, 2, 4, ... 38] in the right order, each matched to its own job.
- Run the whole client in its own separate interpreter process; expect: the monkey patching never leaks into the test runner, and the client exits with return code 0 and no stderr errors.

**Timeout honoured on a pending job**
- Wait with a 2 second timeout on a job id that never runs (unknown or never executed); expect: a timeout error is raised, not a hang and not a return value.
- Measure elapsed time of that wait; expect: at least the requested timeout (2s) and less than timeout + 15s slack (covers the 1 second poll tick and slow host lookup of a new connection).
- Expect the timeout error to actually be raised: if the client printed nothing, the test fails with "wait on a pending job did not raise a timeout".

**Regression guard (lock held while waiting)**
- A background result-listener must not hold the shared subscription lock while it blocks for messages; expect: other waiters can still subscribe, and a waiting call never ignores its own timeout (regression of a hang where the wait never returned).

**Whole-client deadline**
- If the client process does not finish within 2x the result timeout; expect: the test fails with a clear "client did not finish" message rather than hanging the suite.
