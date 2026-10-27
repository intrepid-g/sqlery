### `t/unit/app/test_control.py`

Celery's remote-control surface: asking running workers questions (inspect) and telling them to do things (control). Portable guarantees for sqlery's worker/daemon management API.

**Command coverage**
- Every control command a worker understands has a matching client method; every inspect command a worker understands has a matching inspect method. Expect: no worker command is unreachable from the client API.

**Replies from multiple workers**
- Two workers reply; expect: results merged into one mapping keyed by worker name.
- The same worker name replies twice (duplicate worker names); expect: a duplicate-name warning naming the worker, and the name still present in the result, never a crash.
- Inspect with no target, replies from w1 and w2; expect: a mapping {w1: ..., w2: ...}.
- Inspect targeted at a single worker; expect: that worker's reply unwrapped, not nested under its name.

**Inspect (read-only queries)**
- Ask for active jobs; expect: the query sent as "active"; optional "safe" flag passed through (default unset).
- Ask for scheduled (delayed) jobs, reserved (claimed but not started) jobs, revoked job ids, worker stats, registered job types, active queues, a clock/heartbeat reading, a report; expect: each maps to its own named query.
- Ask for registered jobs with extra info items (rate limit, time limit); expect: those fields requested; with none, an empty list.
- Ask for config; expect: user-set values only by default, defaults included only on request.
- Ask for the status of specific jobs by id: one id, several ids, or a single list of ids (old calling style); expect: all three forms accepted and normalised to a list of ids.
- Worker "hello" announcing itself to a peer; expect: sender name sent, revoked-id list included only when supplied (even a full list of 100 ids).
- Memory sample / memory dump (default 10 samples, custom count honoured) / object-graph dump (defaults: 200 objects, depth 10); expect: diagnostics requests with those defaults.
- Ping with a name pattern and matcher type (regex); expect: the pattern and matcher reach the broadcast.
- Every inspect call carries the inspector's own defaults (destination, timeout, limit, callback, connection) unless overridden per call.

**Broadcast plumbing**
- Broadcast with arguments; expect: sent with those arguments, no destination (all workers), no reply wait by default, default timeout 1 second.
- Broadcast with arguments omitted and an explicit destination list; expect: empty arguments, only the listed workers addressed.

**Control (commands that change worker state)**
- Set a rate limit for a job type (e.g. 100/min); expect: type name and limit sent, to all workers by default or to a named worker, with an optional cap on replies.
- Set soft and hard time limits for a job type; same targeting rules.
- Add a consumer for a queue (exchange, type, routing key optional; default direct) and cancel a consumer for a queue; expect: queue starts/stops being consumed on the chosen worker(s).
- Shut down workers; expect: all or only named workers.
- Ask workers for an immediate heartbeat; enable or disable event emission; expect: all or named workers.
- Restart the worker pool, optionally reloading named modules; expect: default is no reload.
- Grow or shrink the pool by N; set autoscale max/min; expect: values sent to all or named workers.
- Run a leader election on a topic with an action; expect: delivered with the given id.
- Ping all workers; expect: waits for replies, 1 second timeout by default; limit and destination honoured.
- Purge the queue; expect: all waiting jobs removed through the consumer.

**Revoke / cancel**
- Revoke a job id; expect: sent with terminate off and the default terminate signal (TERM).
- Revoke with terminate on, a custom signal (KILL), a destination and a reply limit; expect: all honoured.
- Revoke by header/metadata match (stamped headers) instead of by id; same default and option behaviour.
- Terminate a job by id; expect: equivalent to revoke with terminate on and the TERM signal.
- Revoke from a job result handle; expect: revokes that job id, no reply waited, no signal, no terminate.
- Revoke by header from a result handle; same.
- Revoke a whole group of jobs from the group result; expect: one revoke call carrying all 10 ids.

**Configuration and setup errors**
- Control channel uses the configured job serializer and accepted content types.
- Control channel namespace defaults to "celery" and honours a configured exchange name.
- Control queue durability and exclusivity settings are respected.
- Configure the control queue as both durable and exclusive; expect: a configuration error at startup, because an exclusive queue cannot survive and be shared.

**Process lifecycle**
- After a process fork, the control channel drops its inherited connection/producer pool and builds a fresh one; expect: the child never reuses the parent's connections.
