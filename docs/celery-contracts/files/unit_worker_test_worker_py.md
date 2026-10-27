### `t/unit/worker/test_worker.py`

#### Receiving and claiming jobs
- Enqueue a job (2, 4, 8) for a registered function; worker picks it up; expect it ready to run immediately, running it returns 64, and no scheduled/delayed entry is created.
- Enqueue a job with a future run-at time (tomorrow, ISO string); expect it held in the scheduler (not run now, not in the ready queue), and when released it still runs with the right args (64).
- Scheduled job held for later; expect the worker's capacity (prefetch/claim slot) is increased by one so held jobs do not starve other work; when released into the ready queue, capacity goes back down by one.
- Run-at time that overflows/cannot be converted to a timestamp; expect the job is still acknowledged (not left stuck or crashing the worker).
- Job whose id is on the revoked/cancelled list; expect it is skipped and never reaches the ready queue.
- Message names a job function that is not registered; expect it is not run, nothing queued or scheduled, and the problem is logged (the message is rejected).
- Message that is malformed/invalid (bad kwargs); expect "invalid job message" is logged and the worker keeps going.
- Message with an unknown type/format; expect a warning, no crash.
- Message that cannot be decoded (bad content type/encoding); expect it is acknowledged (dropped, not redelivered forever) and a critical "cannot decode" is logged.
- Rejecting a bad message fails because the connection dropped; expect a warning, a logged reject failure, and nothing run.

#### Connection / storage failures
- Database/broker connection error raised in the poll loop while the worker is running; expect the error propagates to the restart logic (connection errors are retried, other errors surface).
- Storage-channel error in the loop; expect it propagates once, and an unrelated error (SyntaxError) is not swallowed as a connection error.
- Read timeout while polling for work; expect it is ignored and polling continues.
- Hard socket/OS error while polling; expect it is raised while running, but is silent once the worker is closing.
- Error handler: connection errors and channel errors are swallowed; any other exception type is re-raised.
- Connecting repeatedly fails with connection errors; expect retry (with sleeps) until connection succeeds, not a crash.
- Remote-control (inspect/command) message handler: a lookup error is ignored; any other error resets the control channel.
- Resetting the control channel when closing it raises a socket error; expect it is swallowed.
- Control-channel listener on a green-thread pool: drain times out once, then shuts down cleanly; consumer is started, connection is released and closed.
- Stopping the control listener whose thread already stopped; expect no error.
- Start-up loop: when claim capacity changed during iteration, it is pushed to the store before the next iteration; loop iterates until an error, and a socket error from the loop propagates.
- Starting a consumer already marked closed; expect a no-op.
- Reconnecting: connection is re-established, then shut down leaving no connection or task consumer; repeatable (start, restart, stop, shutdown).
- Event dispatcher recreated on reconnect; expect in-flight jobs are re-pointed at the new dispatcher.
- Closing: connection closed and set to none; event dispatcher disabled and heartbeat stopped.
- Reset with no control node; expect no error.

#### Job dispatch to the pool
- Hand a job to the pool; expect exactly one pool execution.
- Pool raises a "revoked" error while dispatching; expect the concurrency slot (semaphore) is released; works with or without a quick-release hook.
- Pool raises Ctrl+C while dispatching; expect it propagates.
- Pool raises a terminate request; expect the process exits.
- Pool raises a regular error; expect it propagates.
- Job taken through the semaphore path; expect it is acquired before processing.
- Pool with no semaphore support; expect jobs go straight to processing.
- Pool with semaphore and event loop; expect a bounded (lax) semaphore is created.

#### Startup, shutdown, termination
- Worker step raises Terminate on start; expect terminate is called on that step. Step raises Shutdown; expect stop is called (works even if the step has no terminate).
- Worker step raises an arbitrary error on start; expect stop with failure exit code.
- Ctrl+C while starting; expect stop with failure exit code.
- Start, then stop with exit code 3; expect every step started, closed and stopped; missing pool or missing (None) step does not break stop.
- Start then terminate; expect all steps terminated; terminating when already in TERMINATE state is harmless.
- Stop/terminate when the worker never started, or from a signal handler when pool is not signal-safe; expect the worker is not marked closed.
- Shutdown with no blueprint, no consumer, or consumer never connected; expect no error and the blueprint is stopped once.
- Terminate with no consumer ever created; expect no error, blueprint stopped once.
- Closing the consumer when it raises AttributeError; expect swallowed.
- Warm (graceful) shutdown; expect the broker socket timeout is NOT lowered, so in-flight acks can still flush (otherwise acks are lost and jobs run twice).
- Cold (immediate) shutdown and terminate; expect the broker socket is bounded by the shutdown timeout so a dead peer cannot hang shutdown.
- Cold shutdown where cancelling the consumer times out; expect active requests are still cancelled and the terminate flag is still set.
- Soft shutdown with running jobs; expect worker waits the configured soft-shutdown timeout. With no running jobs it does not wait, unless "soft shutdown on idle" is enabled (then it waits). Idle with the option off: no wait.
- A shutdown signal is sent to listeners on shutdown.
- Pid file: lock created on start and released on stop.
- Persistent state file option enables persistence of state (e.g. revoked list).

#### Configuration validation
- Concurrency given as int stays; numeric string ("4") is coerced to 4; garbage string falls back to CPU count and logs a warning naming the bad value (deferred to start-up when logging exists).
- Concurrency "auto" (case-insensitive, from CLI or config; CLI wins over config; explicit int wins over config "auto"): CPU-bound pools (prefork, processes alias, dotted path, class, subclass) are capped to the container CPU quota; solo, threads, gevent, eventlet are not capped. Resolution is logged at start (including "no quota found" and "setting only sizes prefork pool"), resolved after worker-init hooks run, and works with autoscale (min/max from autoscale).
- Pool start method defaults to fork, accepts spawn, rejects other values (config error).
- Queue selection: a requested queue that does not exist (include or exclude) is a configuration error; worker-direct option adds the worker's private queue.
- Optional features wire in: embedded scheduler (beat), autoscaler (shares pool with worker).
- Worker has a timer, pool, consumer, and steps; repr is non-empty, str is hostname.
- Timer errors are logged with the exception name; timer ticks are logged at debug with next-wake seconds.
- Stats: when resource usage is unavailable, rusage raises not-implemented but stats still returns.
- Stats report prefetch count and broker info.

#### Resource safety
- Autoscaler/pool event-loop registration survives a file descriptor closed by the OS (no "bad file descriptor" error).
- Event-loop add/remove/reader failures with OSError are tolerated by pool registration, poll start, child tracking.

#### Output safety
- Emergency messages go to stderr by default or stdout when asked, with surrounding newlines.
- Emergency write uses the original OS write, so it works even if os.write was patched to forbid blocking calls in the main loop.
