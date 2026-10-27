### `t/smoke/tests/test_beat_remote_control.py`

Portable guarantee: the scheduler process can be health-checked remotely on the same control channel as workers, and it reports health honestly.

**Liveness / control channel**
- Start the scheduler's remote-control endpoint against a real database/broker, send a ping to it; expect: exactly one reply from the scheduler, "pong".
- Run a worker and the scheduler on the same control channel, ping everyone; expect: both the worker and the scheduler reply (they coexist without interfering).

**Unexpected commands**
- Ask the scheduler a worker-only question (list active jobs); expect: no reply from the scheduler at all (no error row, no noise in worker-only output).

**Stall handling**
- Scheduler's last tick is older than the configured max tick age (60s old vs 5s limit), ping it; expect: no reply (a stalled scheduler must not claim to be alive).
- Scheduler ticks again (fresh tick), ping it; expect: "pong" again (it recovers automatically).

**Startup**
- Scheduler endpoint is given up to 30s to join the control channel before tests proceed; expect: it becomes reachable (readiness is observable).
