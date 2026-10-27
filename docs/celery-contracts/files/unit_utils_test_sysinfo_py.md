### `t/unit/utils/test_sysinfo.py`

Portable guarantee: a worker sizes its own concurrency from the CPU it is really allowed to use (affinity, container quota), never crashes or hangs while probing, and treats bad or missing system data as "no limit known".

**Worker concurrency setting "auto"**
- Setting is "auto" in any case or with surrounding whitespace/newlines; expect: recognised as auto-concurrency.
- Setting is empty, None, "0", "4", the number 4, "autopilot", "auto4", or an arbitrary object; expect: not auto-concurrency, no error.
- The exported auto sentinel constant; expect: always recognised as auto.

**Counting usable CPUs**
- Platform reports a process-specific CPU count; expect: that count wins over affinity and machine totals.
- Process count is None; expect: fall back to machine CPU count.
- No process count but an affinity set of 2 CPUs on an 8-CPU box; expect: 2.
- Affinity lookup raises OSError; expect: fall back to machine CPU count, no crash.
- Platform has no affinity API; expect: machine CPU count.
- Machine CPU count is unknown (None); expect: default of 2.

**Container CPU quota (cgroup v1 and v2)**
- No cgroup files exist; expect: no quota (None).
- Quota file says 2 CPUs worth (200000/100000); expect: 2.0. Fractional 1.5 is honoured.
- Quota is "max" (v2) or -1 (v1); expect: no quota.
- Own cgroup has no limit but a parent has one; expect: the parent limit applies (v1 and v2).
- Several limits along the parent chain; expect: the smallest wins.
- Process list file is unavailable; expect: inspect only the mount root.
- Process in a private cgroup namespace (path "/" or "/../.."); expect: root is used and the ancestor walk terminates at "/".
- v1 mounted only as combined "cpu,cpuacct" with no "cpu" alias; expect: quota still found.
- Process line lacks a cpu controller; expect: ignored, fall back to root quota.
- Garbage in the v2 quota file but valid v1 quota (hybrid host); expect: v1 quota used.
- Garbage in v2 only, or garbage in v1; expect: no quota, no exception.
- Files unreadable (permission denied); expect: no quota, no exception.
- Windows-style path handling (must not loop forever); expect: finishes within 5s, cgroup paths always treated as POSIX, still returns the right quota or None.

**CPU budget for sizing workers**
- Quota checking switched off; expect: budget equals available CPUs, quota not even consulted.
- No quota; expect: budget equals available CPUs.
- Quota 1.5 on 8 CPUs; expect: budget rounded up to 2.
- Quota larger than available CPUs (80 on 4); expect: budget capped at 4.
- Quota below one CPU (0.3); expect: budget 1, never 0.

**Host health helpers**
- Load average reads (0.547, 0.636, 0.699); expect: three values rounded to 2 decimals (0.55, 0.64, 0.7); skipped where unsupported.
- Disk usage for "/"; expect: total blocks, available, capacity and raw stat all populated.
