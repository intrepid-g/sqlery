### `t/unit/test_loops.py`

Celery's only test here covers the synchronous worker loop (`synloop`), the simple blocking consume loop used when there is no event hub. The portable guarantee is clean shutdown: stopping the worker never loses pending bookkeeping.

**Shutdown while running (synchronous worker loop)**
- Start a synchronous worker, then trigger a hard stop (a system-exit signal) while it is polling; expect: the exit propagates to the caller, so the worker does stop and does not swallow the exit.
- Same stop request; expect: pending operations (acks, deferred bookkeeping for jobs already handled) are flushed exactly once before the worker exits, not skipped and not repeated.

**Startup and polling**
- Start a worker; expect: the "ready" hook fires exactly once and the consumer starts consuming exactly once.
- While running, expect: the worker polls the queue or connection with a bounded wait (the heartbeat rate), so it never blocks forever and can notice a stop request.
