### `t/unit/utils/test_debug.py`

Diagnostic helpers (blocking detection, memory sampling, formatting). Little queue behaviour, but portable guarantees for worker diagnostics.

**Blocking detection**
- Run a worker section inside a "block detector" with a 10s limit; expect: an alarm is armed for 10s on entry.
- Leave the block detector (normally); expect: the previous alarm handler is restored and the alarm is reset, so later code is not interrupted.
- The alarm fires while the section is still running (worker stuck); expect: a RuntimeError naming where it is blocked, not a silent hang.

**Memory sampling**
- Take a memory sample; expect: one reading (human-readable resident memory) is appended to the sample list.
- Memory reading when the process handle is unavailable; expect: returns "unknown" (None), no crash.
- Memory reading normally; expect: the process's resident size, formatted for humans.
- Ask for the process handle for the first time; expect: it is created for the current pid and cached for reuse.

**Down-sampling long series**
- 100 items, sample 10; expect: every 10th item (0, 10, ... 90).
- 91 items (not evenly divisible), sample 10; expect: 10 evenly spaced items (0, 9, ... 81), none past the end.

**Number formatting**
- Whole float 10 at precision 5; expect: "10" (no trailing zeros).
- Float 10.45645234234 at precision 5; expect: "10.456" (rounded to precision).
- Byte counts 1MiB, 4MiB, 64KiB, 256 bytes; expect: "1MB", "4MB", "64KB", "256b".
