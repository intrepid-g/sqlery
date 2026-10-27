### `t/unit/worker/test_consumer.py`

The "consumer" is the part of the worker that talks to the broker, claims jobs, throttles them and survives connection loss. For sqlery it maps to the worker's claim loop plus its database connection handling.

**Worker configuration**
- Worker given an explicit heartbeat interval uses it; with none given it falls back to the configured default.
- Looking up a rate limit for a job type that has none yields "no limit", never an error.
- On a cooperative green-thread runtime, the connection timeout setting is switched off to dodge a known hang.

**Claim batch size (prefetch) sizing**
- With no known pool size, updating the batch size does nothing; otherwise it is pool size times the multiplier.
- Reconnect with reduction enabled and an idle worker: keep the full batch size, marked fully restored.
- Reconnect with reduction enabled and N jobs in flight: batch size shrinks by N (never to 0) and is restored gradually.
- Reconnect with reduction disabled: batch size stays full whatever is in flight.
- Reduction is skipped when the broker uses per-consumer limits (and says so in the log); it still runs when limits are channel-wide or unknown.
- If an earlier reconnect left a reduced batch size and the next one takes the skip path, the stale reduced value is reset to full.
- Batch size is derived from configured concurrency, not from the number of jobs running right now (idle worker must not collapse to 1 forever).
- A job arriving whose ack step blows up still leaves the batch size nonzero and restored to maximum.
- ETA/scheduled-job in-memory limit is passed through from config; unset means unlimited; 0 is a valid limit and respected.

**Rate limiting and scheduled (ETA) jobs**
- Rate-limited job whose bucket has capacity: moved to the pool immediately.
- Bucket has no capacity: retry scheduled after the expected wait, with an increasing order priority.
- Empty bucket: nothing is consumed or scheduled.
- Limiting a job adds it to its bucket and triggers scheduling.
- A scheduled job that becomes due decrements the reserve count, joins the bucket, and triggers scheduling.
- Moving a job to the pool marks it reserved and hands it to the executor.

**Event batching**
- Flushing events with no dispatcher is a no-op; with one, it flushes.
- Buffered-event notification with no event loop does nothing; with a loop it schedules a flush.
- Two notifications before the loop turns produce exactly one flush.

**Connection loss and restart**
- Too many restarts in a short window: stop restarting and sleep 1 second.
- Timer entries existing before start are remembered; with no event loop none are recorded.
- Connection fails before the loop starts: entries the connection registered are cancelled, the worker's own survive.
- Once the loop is running, only the loop's entries are cancelled; time limits of still-running jobs survive the reconnect.
- The registered-entries record is reset before each connection attempt.
- Worker already closed: no restart. Worker already terminated: no restart.
- Out of file descriptors: worker terminates instead of retrying.
- Connection error while running restarts the blueprint whether or not connection retry is enabled.
- Channel error restarts only if channel-error retry is enabled; otherwise the error propagates.
- Cleanup of a dead connection is attempted even if it raises (e.g. out of memory); the connection is closed and cleared so restart begins fresh.
- Cleanup always has a socket timeout so a dead socket cannot block forever, including sockets that were already open.
- The error is a short warning; the full traceback is logged only at debug level.
- Retry disabled and connection lost: worker shuts down with a critical log naming the exact setting to change, with wording differing for startup versus mid-run (five setting/phase combinations).
- Each retry logs "trying again in X seconds (n/max)" and counts attempts.
- The connect error callback is safe to call at any time.
- Retry-on-startup unset: falls back to the legacy retry setting and retries.
- Retry-on-startup unset and legacy retry off: deprecation warning, single attempt only.
- Same, and that single attempt fails: error propagates.
- Retry-on-startup explicitly on beats legacy retry off.
- Retry-on-startup off affects only the first connection; later reconnects retry.
- Retry-on-startup off and first connect fails: error propagates, no retry.
- After startup, the legacy setting governs: off means a single attempt that raises.

**Shutdown and cleanup (on close)**
- Closing clears the concurrency semaphore and reserved set, flushes the pool, and tolerates missing parts.
- A job reserved but never started is removed from the request registry (no leak).
- Scheduled (ETA) jobs are likewise removed.
- Their pending timer entries are cancelled through the timer so stale callbacks cannot re-add them.
- If cancelling a timer entry fails, it is logged and cleanup carries on.
- A job both running and scheduled stays visible as running, only the scheduled marker goes.
- With prefetch disabled and concurrency 1, after connection loss a running job still holds the slot: no second job is claimed.

**In-flight jobs on connection loss or shutdown**
- Cancel-on-connection-loss on: unacknowledged late-ack jobs are cancelled; already acknowledged and early-ack jobs are left alone.
- Setting off: a deprecation warning that cancelling will become the default.
- A cancel that raises is swallowed; other jobs are still cancelled.
- Shutdown cancel: unacked late-ack jobs cancelled without republishing; acked ones untouched; early-ack jobs cancelled with retry republish.
- Jobs that already succeeded are never cancelled.

**Queue subscription**
- A re-created consumer does not pick up a queue added after startup.
- Cancelling a queue works by name or alias and stops consuming it.
- Cancelling an unknown queue is a no-op and never creates it, whether auto-create is on or off.
- Re-adding a cancelled queue restores consumption.

**Disable-prefetch mode (claim only when a slot is free)**
- Off: claiming unchanged.
- On: a gate is installed.
- Gate refuses when reserved jobs equal pool size.
- Gate honours the autoscale maximum when lower than pool size.
- Ignored for brokers where it does not apply.

**Pending operations and acks**
- All pending operations run and the queue is emptied.
- They run in scheduling order.
- One raising is logged; the rest still run.
- Without an event loop, ack/reject callbacks run immediately, never deferred.
- With a loop, they go through the loop.
- The wrapped callback is returned, with arguments passed through.
- A raising ack callback is logged, not propagated.

**Heartbeat and events**
- Heartbeat starts when enabled, using the given interval.
- Event dispatcher registers with the loop when heartbeat and loop exist.
- No registration without a loop, or with heartbeat 0.

**Startup steps**
- Stopping the task consumer is safe, including twice.
- Quorum-queue detection: positive, negative, and not applicable on other brokers.
- Global limit is on when detection is off or no quorum queue; off with a quorum queue.
- When global limit is off, one info log says batch size is now static.
- The chosen mode is recorded on the worker for the reconnect path.
- A custom agent is instantiated with the connection.

**Peer sync (revoked jobs)**
- No replies from peers is fine.
- Peers' clocks and revoked ids are adopted, an erroring peer ignored.
- Peers' revoked timestamps are re-stamped locally so they cannot expire wrongly or evict local ids.

**Connection step**
- Shutdown closes and clears the connection; safe when none.
- Close swallows errors from an already-dead socket and still clears.
- Info output strips passwords and censors failover URLs, including a single-string form.
- Start stores the connection.

**Peer gossip and election**
- Join, leave and lost callbacks reach every subscriber.
- Election request is broadcast and tracked.
- Incoming election is tracked and acknowledged; a malformed one is logged, not fatal.
- Acks from unknown elections are ignored.
- Once all peers ack, only the winner runs the action; losers do nothing.
- Winner with no handler: logged, not fatal.
- Joins, leaves and missed heartbeats are logged.
- Re-registering the timer cancels the previous one.
- Periodic check drops dead peers and reports them lost.
- Task events ignored; others routed to handlers, unknown ones tolerated; own clock advanced.
- A peer in another timezone triggers no clock-drift warning.
- A failure launching an elected task is logged, not fatal.

---

_Generated by [Claude Code](https://claude.ai/code)_
