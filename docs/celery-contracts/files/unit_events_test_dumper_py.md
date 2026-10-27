### `t/unit/events/test_dumper.py`

Portable guarantee: an event/audit log printer must render every job and worker lifecycle event as one readable, human-friendly line with host, timestamp, event kind, job identity and extra details. No retry, concurrency or failure semantics are tested here.

**Event naming**
- Worker lifecycle events are shown in friendly words; expect: "worker online" shown as "started", "worker offline" as "shutdown", "worker heartbeat" as "heartbeat".

**Output destination**
- Print a message through the event printer pointed at a custom output stream; expect: the text appears in that stream, not stdout.

**Job event formatting**
- Format a "job succeeded" event for worker1 at 2024-01-01 12:00:00 with job description `mytask(123) args=(1,) kwargs={}` and extra fields result=ok, foo=bar; expect: line has `worker1 [2024-01-01 12:00:00]`, "job succeeded" (dash replaced by space), the full job description, and every extra field as `key=value`.

**Live event handling**
- Receive a "job received" event (unix timestamp, hostname worker1, id abc, name mytask, args `(1,)`, kwargs `{}`); expect: line shows `worker1 [2024-01-01 12:00:00+00:00]` (timestamp converted to timezone-aware UTC), "job received", and `mytask(abc) args=(1,) kwargs={}` built from name, id, args, kwargs.
- Receive a non-job (worker) event "online" with an unknown extra field foo=bar; expect: formatted timestamp and host, friendly name "started", and the unknown field still printed as `foo=bar` (nothing silently dropped).
- Handling an event must not require the caller's event dict to be preserved (the handler may consume fields from it; tests pass a copy).

_Scenarios: 6 (one is a note about input mutation)_
