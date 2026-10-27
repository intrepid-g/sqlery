### `t/unit/concurrency/test_concurrency.py`

**Running a job in a worker slot**
- Run a job with arguments (8, 16) that returns 42, with a completion handler and an "accepted" handler; expect: the accepted handler fires first (told the worker process id and a start timestamp), then the job runs with its arguments, then the completion handler receives the result 42, in that order.
- Run the same job with no accepted handler; expect: job runs, then completion handler gets 42, no error.

**Failure handling**
- Job raises an ordinary error and the caller asked for it to propagate; expect: the error reaches the caller.
- Job raises an ordinary error with no propagation option; expect: the error still reaches the caller (not swallowed).
- Job raises a worker-shutdown request; expect: it propagates so the worker stops gracefully.
- Job raises a worker-terminate request; expect: it propagates so the worker stops immediately.
- Job raises a non-standard fatal exception (not a normal error); expect: it is captured and reported as a failed result via the completion handler, not crashing the slot.
- Job raises such a fatal exception and re-raising the captured failure itself errors; expect: that error surfaces to the caller and the completion handler is NOT called (no false result recorded).

**Worker pool contract (base behaviour)**
- Create a pool of 10 slots; expect: it reports a concurrency of 7 when created with 7, and its info reports its implementation and max concurrency.
- A new pool is not active; once running it is active.
- Starting, stopping, applying, terminating, flushing, soft/hard timeout hooks and event-loop registration are harmless no-ops by default; flush returns nothing.
- Pool reports it started OK by default.
- Closing a pool moves it to closed state and calls its close hook; default close hook does nothing.
- Restarting a pool, or terminating one specific job by id, with no implementation; expect: "not supported" error.
- Submitting work with debug logging disabled; expect: still works.

**Pool selection**
- Platform lacks threading support; expect: available pool names are prefork, eventlet, gevent, solo, processes, custom (no threads).
- Platform has threading support; expect: the same list plus threads, in a stable order.
