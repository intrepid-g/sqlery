### `t/unit/backends/test_redis.py`

Celery's Redis result backend. The Redis wiring is vendor-specific, but the portable guarantees are: results stored/retrievable/expiring, waiters notified exactly once and never deadlocked, connection loss survived, fork-safe, group (chord) completion exactly-once, bad config rejected early.

**Waiting for a job's result (notification channel)**
- Wait on a job, then fork the process; expect: the child drops the inherited connection and subscription, and carries on even if closing the old subscription raises or there was none.
- A job reaching a terminal state (success) stops being watched; a non-terminal state (pending) keeps being watched.
- Poll for events before waiting has started; expect: no crash.
- Subscribing to a job hits a connection error; expect: retried, job ends up watched alongside the earlier ones.
- Unsubscribing hits a connection error; expect: no crash, job no longer tracked locally.
- Cancel a watch while a subscribe is in flight; expect: cancel deferred until the subscribe finishes, then sent (never two operations on the connection at once).
- Cancel a watch while an unsubscribe is in flight; expect: cancel queued and sent afterwards, never nested.
- Cancel a watch while a reconnect re-subscribe is in flight; expect: deferred, then sent; job ends unwatched.
- Cancel a watch while draining events; expect: deferred until the drain finishes, then sent.
- Nested critical sections; expect: only the outermost one sends the queued cancels.
- Reconnect after a drop where one job already finished; expect: it is not re-subscribed, its result is fetched, nothing to unsubscribe.
- Job finished while disconnected but is awaited again before queued cancels flush; expect: it is genuinely subscribed on the new connection (its result must still arrive).
- Body of a locked section raises while a cancel's unsubscribe also fails; expect: original error surfaces, the unsubscribe failure is logged.
- Unsubscribe fails in an otherwise successful section; expect: the error propagates (retry limit exceeded means restart needed).
- Subscribe fails midway; expect: re-entrancy depth reset to zero, the cancel deferred during the failure is not lost.
- A different thread cancels while this thread holds the lock; expect: it is not deferred merely for that reason (only if the lock is actually busy).
- After a fork; expect: re-entrancy depth reset and the lock replaced (the inherited lock may be held by a dead thread).
- Cancel a watch never subscribed; expect: no-op.
- Cancel the same watch twice (garbage collection after get already cleaned up); expect: second is a no-op (this was a real deadlock trigger).
- Drain hits a connection error while a watched job has finished; expect: the finished result is delivered and nothing is re-subscribed.
- Reconnect with nothing watched; expect: no lookup, no subscribe, reconnect hook registered.
- Reconnect where watched job already finished; expect: state change delivered, no re-subscribe.
- Reconnect where watched job is still running; expect: re-subscribed, no hook.
- Reconnect with an older client library signature; expect: no TypeError.
- Waiting finishes for a finished job whose message was buffered unclaimed; expect: leaked buffer entry removed (success, failure).
- Same for revoked; expect: entry kept (waiters may still need it).
- Cleanup when nothing buffered; expect: no-op, no crash.
- Entry vanishes between check and removal (race); expect: error swallowed.
- Every subscribe, unsubscribe and read happens holding the lock.
- 4 threads subscribe/cancel 50 jobs each while another thread drains; expect: never two overlapping calls on the connection.
- Drain with no connection; expect: sleeps outside the lock so others can proceed.
- Stop; expect: connection closed. Stop with no connection; expect: no-op.
- Watch a job with no connection yet; expect: connection started and job subscribed.
- Incoming result message; expect: decoded and state change fired. Non-message traffic (subscribe confirmations); expect: ignored.
- Drainer blocked waiting for data; a new subscribe arrives; expect: subscribe completes without waiting for data.
- Wait for data with any timeout (30s, none); expect: each wait capped at 1s because a reconnect does not wake the poll; short timeouts honoured.
- Two drainers, one consumed the message the other wanted; expect: the other is woken and both finish.
- Subscribe under cooperative (green) threading with several drainers; expect: not starved, drainers finish without error.
- Cancel while another thread holds the lock (finalizer cannot block); expect: returns promptly, the holder performs the unsubscribe on exit.
- Cancel deferred, then the same job is watched again; expect: stays subscribed, nothing pending.
- Connection replaced while a drainer waited; expect: stale connection not read.
- Not yet subscribed; expect: waits for subscription event, no read.
- No readable socket but data available; expect: returns and reads once. No socket attribute; expect: falls back to sleeping 1s.
- Socket readiness check: expect timeout false, readable true, wait-forever works, fallback works without poll, and a closed socket counts as readable.

**Storing and reading results**
- Store success, then read; expect: state and value match. Forget; expect: state back to pending.
- Result expiry honoured on store; unset or zero expiry; expect: no expiry set.
- Failed results use a separate failure expiry; zero means never; defaults to normal expiry; no spurious warning.
- Value above the maximum size; expect: store error raised.
- Expiry accepts number, duration, or default from config.
- Increment and expire pass-through; bulk get works.
- Compression: round trip returns the original, marker present, smaller than plain, binary serializer works, results written before compression was enabled still readable.
- Responses configured as decoded text; expect: compression disabled for that backend only, global setting untouched.
- Cleanup call at process end; expect: harmless.

**Connection config and failure handling**
- Backend object survives pickling.
- Client library missing; expect: configuration error.
- Username/password from config or URL; username absent unless given.
- Credential provider by config or URL (path, instance); expect: no username/password; unknown module raises import error; wrong type raises value error.
- URL parsing: host, port, db, timeouts (config, or coerced from query strings).
- Unix socket URL: path, db, no host/port.
- TLS: options from config or URL, scheme variants, string forms of certificate requirement, TLS options even with plain scheme; invalid or missing requirement on TLS scheme; expect: value error; requirement on plain scheme; expect: value error.
- Health check interval and client name passed through, absent when unset.
- Driver identification: uses modern info when available, falls back to name/version, "unknown" if version missing, also on attribute error.
- Config missing optional keys; expect: still builds.
- Connection lost; expect: error logged with attempt number and wait, backoff intervals used in order.
- Retry policy from config (max retries, start, step) passed to retry loop.
- Only connection and timeout errors are safe to retry; generic and other client errors are not.
- Extra retryable errors by class, dotted path, scalar, passed to the waiter; empty or unset adds none; non-exceptions, non-types and unimportable paths ignored.

**Group completion (chord: dependent job runs after N jobs)**
- Group results saved only when the header holds nested groups, never for simple results; saved with chord expiry.
- Group size stored with expiry, configurable (number, duration, zero means none).
- Parts return in random order, 10 of 10; expect: results collected, temp keys deleted, and given expiry.
- Ordered and unordered modes both work; ordered returns by header position.
- No or zero expiry; expect: no expiry set.
- 2 parts return; expect: dependent job not run after the first, run once with [10, 20] after the second.
- Dependent job fails to enqueue; expect: dependent marked failed, not lost.
- Storage error (group error or other) while recording a part; expect: dependent marked failed.
- Part without group or id; expect: ignored.
- Duplicate part (re-run with other value, idempotency early return, worker lost failure); expect: counted once, first result wins, dependent runs once, late duplicates ignored.
- Chord already failed, then a copy of a header part arrives (or reading results fails oddly); expect: error reported once, results kept, dependent never run.
- Last part recorded but its worker died before enqueuing the dependent; expect: redelivery still enqueues it.
- Same job id at two positions; expect: both counted, dependent runs once with both.
- Part recorded by an older version without done marker; expect: not stalled.
- Real script: duplicates, ordering with and without index, same id twice; expiry on all keys (group key at least one day).
- Complex header: result join waits up to the join timeout, restores group, native join if supported else normal join; dependent receives joined value.

**Sentinel (high-availability) mode**
- Backend pickles; client missing; expect: configuration error.
- Multiple sentinel hosts in URL; expect: all parsed with port/db/password; password hidden in displayed URL unless asked.
- Username and password, or password only, passed to master connection.
- TLS settings applied with sentinel-aware connection.

---
_Generated by [Claude Code](https://claude.ai/code)_
