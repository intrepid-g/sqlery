### `t/unit/app/test_amqp.py`

Celery's message-building, publishing, routing and queue-registry layer. Portable guarantees only.

**Bad input at enqueue time**
- Enqueue a job whose kwargs are a list or tuple, not a mapping; expect a type error and nothing enqueued.
- Enqueue a job whose args are a bare string; expect a type error and nothing enqueued.
- Enqueue with a negative delay; expect a value error and nothing enqueued.

**Delays, run-at and expiry**
- Enqueue with a 10s delay; expect run-at = now + 10s.
- Enqueue with a 30s expiry; expect expires-at = now + 30s.
- Enqueue with an explicit run-at datetime; expect it stored unchanged.
- Enqueue with run-at or expiry already given as ISO strings; expect them stored unchanged.
- Enqueue with delay/expiry when UTC mode is off; expect it still works.
- Enqueue at 01:30 local with a 90 min delay (or expiry) across spring-forward DST; expect exactly 90 real minutes elapse, not 90 wall-clock minutes.
- Same across fall-back DST; expect exactly 90 real minutes.
- Enqueue during the second, repeated 01:30 hour with a 15 min delay (or expiry); expect exactly 15 real minutes.

**Argument previews (for logs and dashboards)**
- Enqueue with deeply nested args; expect the stored preview truncated past the default depth ("{...}").
- Raise the configured depth; expect the preview to show more.
- Set depth to 0 or none; expect an unlimited preview.
- Same three rules for kwargs previews.

**Dependencies and follow-ups**
- Enqueue a job with success follow-ups, failure follow-ups and a final "chord" step; expect all stored in order with the job.

**Publishing**
- Publish with extra properties (delivery mode, timeout, confirm timeout, custom headers); expect all passed through to the transport.
- Publish with no compression given; expect the configured default. Publish with an explicit one; expect it to override.
- Publish with a partial retry policy; expect it merged over the configured defaults (override one key, keep the others).
- The "sent" notification uses the same merged retry policy as the job.
- A before-publish hook sees the merged retry policy.
- A hook that mutates the retry policy affects that publish only; expect the global defaults unchanged.
- Close the event dispatcher, then publish; expect the job and its "sent" event both still published and the dispatcher reopened.
- A faulty or odd "sent" signal receiver is registered; expect publishing still succeeds.
- Publish to a named queue; expect routing to that queue on the default exchange, ignoring stray exchange or routing-key arguments for direct exchanges.
- Publish to a declared queue with auto-create disabled; expect it works.
- Publish to a broadcast queue; expect the sent event to carry the right exchange and routing key.

**Routing config**
- Routing rules are cached between sends.
- Change routing rules at runtime (mapping, pairs, keyword, legacy key or namespaced key); expect the router rebuilt once and the next job routed by the new rules.
- Change an unrelated setting; expect the router not rebuilt.

**Queue registry**
- A new registry with no queues is empty; its formatted listing is empty text.
- Add a queue; expect default exchange and default routing key filled in, explicit ones preserved.
- Add via add() or via item assignment; expect identical treatment.
- Set a max priority; expect it applied to queues without their own, explicit per-queue values kept, none when unset.
- Queue with an alias; expect lookup by alias or real name to give the same queue.
- Select queues to consume, add one more; expect all consumed. With no selection, adding extends the default set.
- Deselect a queue by name or alias, selected by either; expect it removed.
- Deselect with no explicit selection; expect it excluded, and routing-only queues stay unconsumed.
- Deselect an unknown queue; expect nothing created, with auto-create on or off.
- Look up a missing queue with auto-create on; expect it created, classic or quorum as configured, with the configured exchange type.
- Configure an invalid auto-create queue type; expect a clear value error on lookup.
- Configure the default queue (name, exchange, routing key, classic or quorum); expect exactly one default queue with those properties.
- Configure the default exchange or exchange type; expect queues to use it.

**Worker side and connection pooling**
- Restrict accepted content types in config; expect the consumer accepts only those, and a per-consumer override is honoured.
- Producer pool with no limit; expect it follows the connection pool's limit, releases and re-acquires work.
- Producer pool with limit 2; expect released producers are reused, not recreated.
