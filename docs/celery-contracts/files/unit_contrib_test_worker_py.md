### `t/unit/contrib/test_worker.py`

**Embedded test worker (start/stop around a block)**
- Start a worker inside a test block, enqueue `add(1, 2)`, wait up to 5s for the result; expect: result is 3, worker shuts down cleanly when the block ends.
- Start a worker, enqueue a job that raises; expect: the error surfaces to the caller waiting on the result (same exception type), and the worker helper does not hang on exit.
- Start a worker with a custom hostname/worker name; expect: the worker reports exactly that name, and jobs still run and return correct results.

**Test-harness teardown (cached state)**
- Set up a default app/backend (both thread-safe and non-thread-safe result backend modes), use it, then tear down; expect: the cached result backend is cleared (nothing leaks into the next test), in both modes.

**Worker log handling by pool type**
- Create a test worker with a process-pool ("prefork"-like) setting; expect: a log queue and listener are created so child-process logs reach the parent, and this works even when the optional traceback-pickling library is missing.
- Create a test worker with an in-process pool (solo) or green-thread pool (gevent); expect: no log queue is created.
