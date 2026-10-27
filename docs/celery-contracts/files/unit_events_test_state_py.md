### `t/unit/events/test_state.py`

Celery's in-memory monitor state: it rebuilds a picture of workers and jobs from a stream of lifecycle events. The portable guarantee for sqlery: job and worker status derived from recorded events must be correct, order-tolerant, bounded in memory and crash-proof on bad input.

**Worker identity and liveness**
- Two workers with the same hostname are equal and hash alike; different hostnames differ; a worker never equals an unrelated object.
- Heartbeat expiry = last heartbeat time + frequency + grace window, also correct when times come as high-precision decimals.
- Worker comes online with decimal-typed timestamps and frequency; expect: accepted, worker alive.
- Worker online then offline; expect: alive, then not alive.
- Worker whose only heartbeat is older than the expiry window; expect: not alive. A fresh heartbeat arrives; expect: alive again.
- Heartbeat event with no timestamp; expect: no crash, no heartbeat recorded.
- Event timestamp far ahead of local clock; expect: a "substantial drift" warning is emitted.
- Each heartbeat is recorded; history stays ordered by receipt time even if one arrives out of order.
- Offline event for a worker never seen; expect: no crash, worker not registered, leave callback still fired, subject "offline".
- Online event for a new worker; expect: created, subject "online", join callback fired.
- Unknown worker event types, with or without a hostname; expect: ignored, no crash.
- Get-or-create worker: first call creates, second returns the same worker; supplied attributes (e.g. pid) overwrite on the existing worker.
- Three workers online; expect: three alive.
- Worker saved and restored (pickle-style) keeps hostname, pid, frequency, heartbeats, clock, active, processed, load average and software version.
- Worker accepts arbitrary extra attributes on update (idx, clock, custom).

**Job identity and state**
- Jobs with the same id are equal and hash alike; different ids differ.
- Job info exposes the full documented field set; can be narrowed to chosen fields, extended with extras, and an unknown field name yields nothing.
- Job saved and restored equals the original.
- Job is "ready" only after a terminal state (succeeded); merely received is not ready.
- A "sent" event leaves the job pending.
- Events arrive out of order (failed, started, then received with name/args); expect: state stays failed, name and args are merged in. A later retried event moves state to retry.
- Full lifecycle received, started, revoked, retried, failed, succeeded; expect after each: matching state, its timestamp set and equal to the latest timestamp, worker recorded; retry/failure keep exception and traceback; success keeps result and runtime.
- A job event from a worker not yet seen; expect: that worker is registered and alive.
- Event of unknown job type; expect: no crash.

**Parent, child, root**
- Child job received with parent and root ids; expect: parent and root links resolve, child listed under the parent's children.
- Child arrives before its parent; expect: links still established once the parent appears.

**Ordering**
- Interleaved events from several workers with logical clocks, replayed 1000 times with shuffled ids and rising clock offsets; expect: jobs always listed in logical-clock order (A, C, B), independent of wall time.
- Ascending-order listing: a known unfinished/skipped case upstream (not guaranteed).

**Queries**
- Get-or-create job: created flag first time, same object after.
- 20 jobs over two types and two workers: list newest-first and oldest-first (20 each); filter by type (10/10, also via indexing); filter by worker (10/10); list distinct job types.
- Limit on iterating jobs; expect: only that many returned.

**Memory bounds and cleanup**
- Max jobs in memory = 1 (heap multiplier 2), three jobs arrive; expect: oldest dropped, only newest two retained by clock; a duplicate heap entry still lists fine.
- 100 children whose parents never appear, limit 10; expect: pending-parent bookkeeping never exceeds the limit (no leak in long-running monitor).
- Clear: counters and workers reset, jobs kept by default; clear with "not ready only" off removes all jobs.
- Clear only jobs with ready=False; expect: job table empty.
- Run a snapshot action with clear-after; expect: event counter reset. Without clear-after; expect: counter kept.

**Callbacks, copying**
- Per-event callback is invoked for every event received.
- Whole state can be pickled and deep-copied after processing events.
- State, worker and job have printable representations.
