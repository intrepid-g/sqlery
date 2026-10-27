### `t/unit/worker/test_heartbeat.py`

**Worker heartbeat lifecycle**
- Start a worker's heartbeat with a 1s interval; expect: a repeating heartbeat is scheduled. Stop it; expect: the schedule is cleared. Stop again; expect: no error (stop is idempotent).
- Start the heartbeat; expect: an "online" event is announced immediately, before any periodic heartbeat.
- Trigger one heartbeat tick; expect: a "heartbeat" event is sent.
- Stop the heartbeat; expect: a final "offline" event is sent.

**Event reporting disabled**
- Start the heartbeat while event reporting is disabled; expect: nothing scheduled, nothing sent.
- Stop the heartbeat while event reporting is disabled (never started); expect: nothing sent, no error.

**Delivery reliability**
- Periodic heartbeat events are sent with retry enabled (a transient send failure is retried rather than losing the heartbeat).
- The final "offline" event is sent with retry disabled (shutdown must not hang retrying a dead connection).

**Observability hook**
- Sending a heartbeat event fires a "sent" signal with the heart as sender; if no signal hook is configured, sending still works without error.
