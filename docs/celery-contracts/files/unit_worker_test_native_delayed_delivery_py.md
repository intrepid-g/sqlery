### `t/unit/worker/test_native_delayed_delivery.py`

Portable guarantee: when a worker starts, it prepares whatever the backend needs so scheduled (not-yet-due) jobs are held and released on time instead of blocking the worker. Setup is validated, retried on transient failure, partially tolerant of per-queue failure, and loud (logged) when it cannot complete.

**Activation**
- Start a worker on a setup where the delayed-delivery feature is not supported; expect: component not activated.
- Start a worker on a setup where it is supported; expect: component activated.

**Per-queue routing behaviour at startup**
- Queue uses direct routing (no delayed support); expect: exactly one warning saying scheduled jobs for it will block the worker until due, nothing else.
- Queue uses topic routing; expect: no warnings, one connection opened and closed cleanly.
- Queue uses fanout routing; expect: no warnings.
- No queues configured; expect: one warning "no queues found to bind", no crash.
- Ephemeral broadcast queues alongside a normal queue; expect: only the normal queue is bound, broadcast is skipped.

**Configuration validation (bad input)**
- Broker URL given as one string, a ";"-separated string, or a list; expect: the set of unique URLs.
- Broker URL empty string, null, or empty list; expect: error "configuration is empty".
- Broker URL is a number; expect: error "must be a string or list".
- Broker URL list containing a non-string (number, null); expect: error "all URLs must be strings".
- Queue type unset or empty; expect: error "not configured".
- Queue type unknown value; expect: error naming allowed values (classic, quorum).
- Queue type classic or quorum; expect: accepted.
- Start with invalid config; expect: one CRITICAL "configuration validation failed" log AND the error is raised to the caller (not swallowed).

**Retries and fallbacks when the backend is unreachable**
- Two broker URLs, both refuse connections; expect: each URL tried, one warning per failed URL, one CRITICAL at the end, worker start does not crash.
- Retry notification fired; expect: warning containing attempt "n/total" and the underlying error text.
- Retry callback contract: receives the exception, an iterator of float delays, and an integer attempt count; returns a float delay; with permanent failure it is called exactly max-retries times and the final error propagates.
- Binding fails twice with a transient connection error then succeeds; expect: 3 attempts total, 2 retry warnings numbered attempt 1 and attempt 2, setup completes.
- Declaring the delay infrastructure fails (generic error); expect: warning per URL, one CRITICAL "failed for all broker URLs", "failed to declare" logged.
- Binding fails (generic error); expect: warning per URL, one CRITICAL, "failed to bind queue" logged.

**Partial failure across multiple queues**
- Three queues, the second fails to bind (not found); expect: all three still attempted, the failure logged as an error naming the queue, others bound.
- Three queues, first and third fail with different errors; expect: all three attempted, then one aggregate error listing both failures (count 2, original types and messages preserved).
