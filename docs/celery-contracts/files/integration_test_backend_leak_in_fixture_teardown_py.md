### `t/integration/test_backend_leak_in_fixture_teardown.py`

**Result storage**
- Start a worker, enqueue 1 job that returns "stored", wait up to 10s for its result; expect: the result is "stored" and the wait does not hang.

**Resource cleanup on shutdown**
- Repeat 20 times: build a fresh app, start a worker, run 1 job, stop the worker and drop the app; expect: the result-store connections opened by the runs are not all still open afterwards (at least some are released or reclaimed).
- Stopping a worker and discarding its app must not leave the app, or its result-store connections, referenced and open (no connection leak per start/stop cycle).

**Isolation**
- Each cycle uses its own app and worker; expect: no state carried between cycles.

**Unsupported setup**
- Run the test against a result store that has no connection pool to inspect; expect: the scenario is skipped, not failed.
