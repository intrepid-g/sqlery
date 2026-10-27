### `t/unit/worker/test_autoscale.py`

**Wiring (autoscaling worker pool)**
- Autoscaling on, no event loop: starting the worker registers the scaler's "task received" hook and starts the scaler exactly once.
- Custom scaler with no "task received" hook: start still works; nothing is registered; scaler is started once.
- Event-loop mode: the scale check is registered for task arrival and repeated on the keepalive interval.
- Worker info report includes the autoscaler's details (min, max, current).

**Scaling behaviour**
- Idle worker with min 3, max 10: pool stays at 3.
- 20 jobs reserved: pool grows to max 10 (never beyond). Reservations cleared: pool stays at 10 until keepalive elapses, then shrinks to 3.
- Keepalive after activity: grow to 10 at t=100; a job arrives at t=200 (10 reserved) so the idle clock restarts; at t=201 and t=230 (keepalive 30) still 10; at t=231 shrinks to 3. Same whether the check runs on task receipt or from the event loop.
- A custom scaler that records its own last-scale-up time is honoured: scale up 7 at t=100, scale down at t=131 with keepalive 30 returns to min 3.
- Across 35 jobs arriving one by one, then completing one by one, pool size always stays within [min, max], never negative.

**Live reconfiguration**
- Changing max/min at runtime resizes the pool (max 7 -> 7 processes; min 6 -> 6 processes) and updates the stored limits; large, small and None values are accepted without error.
- Changing limits adjusts the consumer prefetch count by the delta (max lowered by 5 -> -5; raised -> +10), identically for prefetch multiplier 1 and 4.

**Concurrency and shutdown**
- Receiving a job never blocks while a pool resize holds the lock (no waiting on the resize).
- Stop: the scaler thread is joined only if it is alive; if already dead, no join.
- Run loop executes the scaling body, then marks itself shut down and stopped.

**Unexpected problems**
- Pool shrink raising an unexpected exception is swallowed; scaler keeps working.
- Pool shrink raising ValueError is logged at debug level and not propagated.
- Scaler thread crashing (e.g. OSError): error written to stderr and the process exits with code 1 (no silent zombie worker).

**Prefetch limit**
- With prefetch disabled, consumption is capped by the autoscale max, not the current pool size: max 5 with 5 reserved, cannot take another job; with 4 reserved, can.
