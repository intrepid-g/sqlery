### `t/unit/worker/test_control.py`

Portable guarantee: a running worker can be inspected and steered remotely (control commands), and cancelling jobs is safe, idempotent and never corrupts results.

**Control channel lifecycle**
- Shut down a worker's control listener; expect: listener cancelled (errors ignored) and channel closed.
- Reset the control listener; expect: the old listener cancelled exactly once, only the new one live.
- Reset 10 times while the message handler keeps raising; expect: every previous listener cancelled exactly once, only the latest live, worker keeps going.
- Stop the async-style control listener twice; expect: first stop signals shutdown, waits and closes; second stop is harmless.
- Reset counter increments per reset; the listen loop re-establishes its connection on each reset (4 setups for 3 resets plus the initial).

**Discovery, health, shutdown**
- Ping a worker; expect: "pong", and the reply is sent to the reply-to address keyed by worker hostname.
- Send an unknown command; expect: rejected (KeyError). Send a command addressed to another host; expect: ignored.
- Send shutdown; expect: clean exit with code 0.
- Hello from itself; expect: no reply. Hello from another worker; expect: reply with logical clock incremented and its revoked ids, merged with the ids the peer sent.
- Hello with an expired revoked id in the set; expect: expired id not sent.
- Receive revoked ids stamped far in the future by a peer; expect: re-stamped locally, they expire normally and never evict newly revoked ids.
- Ask for clock, config dump, report; expect: values returned, no error.
- Election request; expect: forwarded to peer-coordination if present, silently ignored if absent.

**Events and heartbeat**
- Enable events when none/worker-only/already task-enabled; expect: no-op / task group added / "already enabled".
- Disable events twice; expect: task group removed, then "already disabled".
- Enable/disable/heartbeat with no event dispatcher; expect: "unavailable" error (heartbeat: no crash), never AttributeError.
- Heartbeat command; expect: a worker-heartbeat event emitted.

**Introspection**
- Stats; expect: total processed count.
- Active queues; expect: names of consumed queues; none when no consumer.
- Dump registered jobs; expect: names with default info (e.g. rate limit); with empty defaults the info is omitted.
- Dump jobs with chosen public attributes (rate_limit, max_retries); expect: values shown.
- Request dunder attribute (__dict__); expect: refused, no secrets leaked.
- Request private attribute; expect: never read, property getter never executed.
- Dump active jobs; expect: listed; safe mode shows repr of kwargs only.
- Dump reserved (prefetched) jobs; expect: name and hostname listed, empty once cleared.
- Dump scheduled (ETA) jobs; expect: empty first, then listed, tolerating non-job entries.
- Query job by id; expect: unknown id gives nothing; reserved shows "reserved"; while running "active"; back to "reserved" after; a job waiting on ETA shows "scheduled" and is not counted as reserved.
- Dump revoked ids; expect: exactly those revoked.

**Runtime tuning**
- Set hard/soft time limit on a job type; expect: applied; None clears; unknown job name gives error.
- Set rate limit "100/m" then 0; expect: applied, limiter reset each time; invalid string gives "Invalid rate limit"; unknown job name causes no crash.
- Grow/shrink worker pool; expect: size and prefetch change by multiplier; unsupported pool gives error.
- Add consumer for a queue; expect: consuming starts; adding twice is harmless; cancel stops it.
- Autoscale min/max; expect: ok with new bounds; no autoscaler gives error.
- Pool restart when disabled in config; expect: ValueError. Enabled; expect: pool restarted, rate limits reset, strategies updated, no module reload by default.
- Pool restart where pool does not support it, or no controller; expect: tolerated, no crash.
- Pool restart with module list; expect: modules imported, or reloaded only when reload requested.

**Revoke (cancel) jobs**
- Revoke by id (with or without known job name, even unregistered name); expect: id recorded as revoked. Revoke addressed to another worker; expect: not recorded.
- Revoke with terminate for a running job; expect: job terminated, reply says terminated; unknown id is only revoked ("tasks unknown").
- Revoke without terminate while job is running; expect: job left running, no result write.
- Revoke with terminate while running; expect: job terminated, but the terminating worker writes the state, not the controller.
- Revoke the same reserved job twice; expect: marked revoked in the job's own result store, per call, not the default one.
- Revoke a dependency-group (chord) member once or repeatedly; expect: state REVOKED, group counter counted exactly once, no double count on later revoke or discard.
- Revoke several ids; expect: each marked revoked in result store.
- Revoke an id whose result is already finished (any ready state); expect: result kept, not overwritten with REVOKED.
- Revoke an unfinished id (pending, retry, etc.); expect: marked REVOKED. Terminate on pending id: also marked.
- Revoke an id running elsewhere (started/custom progress), with or without terminate; expect: recorded revoked locally, state untouched.
- Result-store state lookup fails; expect: id still revoked, no crash.
- Result-store write fails; expect: id still revoked, no crash.

**Revoke by stamp (metadata tag)**
- Revoke by tag with scalar, list, tuple, set or mixed-type values, terminate on or off; expect: tag and values recorded and all matching jobs affected.
- Revoke by tag with terminate; expect: reply lists tag and values terminated, matching running job terminated once.
- Matching job whose stamp value is missing (None); expect: no TypeError, job not terminated, reply says "were not terminated".
- Job with several tags, first missing but later one matching; expect: later tags still checked, job terminated.
