### `t/smoke/tests/test_gossip.py`

Portable guarantee: workers announce themselves and peers learn about them correctly, regardless of the clocks and timezones of the machines involved.

**Worker discovery across timezones**
- Start a worker on a machine in one timezone; start an observer (a second process watching worker heartbeats) in a different timezone; expect: the observer learns of the worker within 10 seconds, otherwise the test fails with "worker event never reached observer".
- Same setup; expect: the observer records that the worker's UTC offset differs from its own (proves the timezones really differ).

**Time handling**
- Same setup; expect: the heartbeat timestamp is kept as an absolute point in time, so the gap between "sent at" and "received at" is within the allowed heartbeat drift (not hours off because of the timezone difference).
- Same setup; expect: no clock-drift warning is emitted (the system must not merely suppress it; the timestamp itself must be correct).

**Cleanup and robustness**
- Whether the test passes or fails, the observer is always stopped and the process timezone is restored, so one scenario cannot leak state into the next.
- Polling for events tolerates read timeouts (idle intervals) without failing, only the overall 10-second deadline fails.
