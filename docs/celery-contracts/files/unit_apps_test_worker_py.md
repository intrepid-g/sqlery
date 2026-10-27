### `t/unit/apps/test_worker.py`

**Startup purge of queued jobs**
- Worker starts with "retry on startup" enabled and max retries 7; purge runs; expect: database connection is retried up to 7 times (not a single bare connect).
- Worker starts with "retry on startup" disabled; purge runs; expect: exactly one plain connection attempt, no retries.
- Worker starts with the newer retry-on-startup setting unset but the older general retry setting enabled; purge runs; expect: retry behaviour follows the older setting (backward compatibility).

**Worker initialisation**
- Construct a worker; expect: default setup runs exactly once (not twice).
- Subclass overrides default setup (e.g. sets concurrency 7) and is constructed with concurrency 2; expect: the override wins, concurrency is 7.

**Shutdown banner printing (best effort)**
- Print a shutdown message; expect: it is written through the original low-level write, and the text is present.
- The write fails (e.g. broken pipe); expect: error swallowed, caller's flow is never aborted.

**Signal-driven shutdown (warm and cold)**
- Warm shutdown signal arrives but printing the banner raises; expect: the error propagates, yet the "should stop" flag is still set (exit code 0).
- Cold shutdown signal arrives but the shutdown callback raises; expect: the error propagates (not swallowed), yet the "should terminate" flag is still set.
- Cold shutdown with a callback that waits out the soft-shutdown timeout; expect: while the callback runs, "should terminate" is still unset (so running jobs' timeout/failure handling does not yet see a hard terminate); after the callback, it is set.
- Normal warm shutdown with no errors; expect: banner printed once, shutting-down notification sent once, "should stop" set.
- Signal received in a child pool process (not the main process); expect: no banner, callback not run, but the stop flag is still set.
