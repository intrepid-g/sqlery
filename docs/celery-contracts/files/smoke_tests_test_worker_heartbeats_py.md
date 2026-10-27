### `t/smoke/tests/test_worker_heartbeats.py`

**Connection liveness (heartbeats)**
- Start a worker with a heartbeat interval configured; expect: the broker sees exactly 3 worker connections, 2 of them using the configured heartbeat interval (main consumer connection and event/monitoring connection) and 1 with no heartbeat. Connections may take a while to appear, so allow a retry window (up to ~30s).
- Freeze the worker process (suspend, not kill) so it can no longer send heartbeats; expect: within roughly 5x the heartbeat interval, the broker drops the 2 heartbeat-enabled connections and only the non-heartbeat connection remains (dead workers do not leave stale connections open).
- Always resume the frozen worker afterwards, even if the assertion fails (cleanup is guaranteed).

**sqlery translation**
- Worker with a heartbeat interval; freeze it; expect: after the lease/heartbeat TTL passes, its worker row is marked stale or dead, and any running jobs it held are detected as orphaned and recovered (requeued or failed).
- Worker that sends heartbeats regularly; expect: it stays alive and keeps its job leases.
