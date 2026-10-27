### `t/unit/backends/test_asynchronous.py`

Portable guarantee: the loop that waits for job results must finish when results arrive, time out cleanly, survive flaky database/broker connections, never starve other work, and fail loudly (never hang) once its background helper has died or been stopped.

**Waiting for a result (synchronous waiter)**
- Wait on a result that becomes ready after a few polls; expect: wait ends, result ready.
- Wait with timeout 0 on a result that is not ready; expect: timeout error raised, no polling done.
- Wait with timeout 0 on a result already ready; expect: returns immediately, no polling done.
- Wait while result arrives late; expect: the on-interval hook is called on every iteration (at least twice over 3 polls).
- Result never arrives and total elapsed time exceeds the timeout; expect: timeout error, result still not ready.
- No custom poll function given; expect: the consumer's own event-draining is used.
- A poll raises an idle timeout twice, then result arrives; expect: idle timeouts silently swallowed, wait ends ready.
- A poll raises a connection error (OSError) twice, then result arrives; expect: each logged as a warning, loop continues, result ready.
- A single connection error then success; expect: exactly one warning ("connection error during drain_events"), exactly one backoff sleep of one interval, result ready.
- wait_for with a timeout; expect: the poll callback is called once with that timeout.

**Background drainer (green-thread/greenlet style worker)**
- Drain loop is told to stop after a few cycles; expect: exits cleanly, shutdown flagged, no error recorded.
- Drain raises idle timeouts 3 times, then stops; expect: timeouts swallowed, no error recorded.
- Drain raises connection errors 3 times, then stops; expect: 3 warnings, a backoff sleep per error, loop continues, no error recorded.
- Drain raises an unexpected error (not connection/timeout); expect: error stored, re-raised to caller, shutdown flagged.
- Check-not-shut-down after a failure; expect: the stored original error is re-raised.
- Check-not-shut-down after a clean stop; expect: "restart required" error.
- Check-not-shut-down while running; expect: no-op.
- Start the drainer; expect: it is spawned, waits for the started signal, and ends shut down once stopped.
- Start a drainer that has already shut down; expect: "restart required" error.
- Stop the drainer; expect: stop flag set and call waits for shutdown confirmation.

**Real concurrency (default threads, gevent, eventlet)**
- Result fulfilled by another thread after a short delay; expect: wait ends ready, on-interval calls bounded (under 20).
- Other concurrent tasks run while waiting; expect: they keep being served (at least as often as the interval hook), the waiter never blocks the event loop.
- Result never fulfilled, timeout several intervals; expect: timeout error, result unfulfilled, on-interval calls bounded.
- (Greenlet runtimes) Background drain loop already died with an error before waiting; expect: waiter raises that same error instead of hanging.
- (Greenlet runtimes) Drain loop dies while a waiter is waiting; expect: waiter raises that error.
- (Greenlet runtimes) Start after a previous run failed; expect: start raises the earlier error.
- (Greenlet runtimes) Start after the drainer was stopped; expect: "restart required" error.
- (Greenlet runtimes) One simulated connection error in the drain loop; expect: warning logged ("connection error during drain_events"), loop carries on, no error stored.

**Reconnect on connection loss**
- Block runs without error; expect: passes through untouched.
- Block raises an unrelated error (e.g. ValueError); expect: propagates unchanged.
- Default consumer has no known connection error types; expect: any error propagates (no reconnect attempted).
- Block raises a known connection error; expect: reconnect called exactly once, error swallowed.
- Reconnect itself fails with a connection error; expect: RuntimeError "Retry limit exceeded".
- Reconnect failure case; expect: the RuntimeError is chained from the reconnect failure for diagnosis.
- Base reconnect implementation; expect: harmless no-op returning nothing.

**Joining results**
- Group containing a task and a nested chain-of-group, already complete, join with timeout 0 or 10; expect: draining skipped yet all nested results returned in order (`[101, [1001, 2001]]`).
