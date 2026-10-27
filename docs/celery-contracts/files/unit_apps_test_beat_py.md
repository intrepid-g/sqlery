### `t/unit/apps/test_beat.py`

**Scheduler launcher: settings reach the scheduler (remote control)**
- Start the scheduler process with remote control on and a custom node hostname; expect: the scheduler service receives exactly those values.
- Start the scheduler with no remote-control or hostname option; expect: the service receives "unset" (None, not False), so it falls back to configuration. A launcher must be able to say "no opinion".
- Unknown or unthreaded options must never be silently dropped between the launcher and the scheduler; each supported option is explicitly passed through (guards against a flag that is accepted but ignored).

**Startup banner**
- Remote control disabled; expect: banner shows no remote-control line (empty).
- Remote control enabled with hostname X; expect: banner has a remote-control line naming node X.

**Portable note:** the file is small and launcher-specific; the portable guarantees are "CLI/config options are faithfully propagated to the scheduler, defaults defer to settings, and operator-visible startup info reflects the real state".
