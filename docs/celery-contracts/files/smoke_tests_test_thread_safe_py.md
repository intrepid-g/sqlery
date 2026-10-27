### `t/smoke/tests/test_thread_safe.py`

**Concurrent enqueue from threads**
- Enqueue one job from a single thread, on a queue served by one worker; expect: the "job enqueued" hook fires exactly once.
- Enqueue jobs from 2 threads at once; expect: hook fires exactly 2 times (no lost or doubled notifications).
- Enqueue jobs from 42 threads at once (more threads than the default connection pool would suit, pool limit raised to 42); expect: all 42 enqueues succeed, hook fires exactly 42 times, no pool exhaustion or deadlock.
- Run the same concurrent-enqueue scenarios with a single worker and with two workers serving the same queue; expect: identical behaviour.
- Each thread sets up its own app/backend context before enqueueing; expect: enqueue works from any thread without shared-state corruption.

**Closing an event/notification sender while a send is in flight**
- Start sending a lifecycle event (blocked mid-publish), then call close from another thread; expect: close waits and does not return until the in-flight send finishes.
- After the in-flight send is released; expect: the event was delivered exactly once, and close completes.
- After close; expect: the sender's producer is detached (none).
- Send an event after close; expect: no error and nothing published (silently dropped).
- After close, publish an event explicitly with a caller-supplied producer; expect: it is still delivered (closing does not break explicit producers); total delivered: in-flight event, then explicit event, in order.
- Blocked publish never released within 10s; expect: a timeout error is raised rather than hanging forever.
