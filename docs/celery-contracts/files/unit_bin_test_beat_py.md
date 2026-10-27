### `t/unit/bin/test_beat.py`

Portable guarantee is thin: this file only tests the scheduler's CLI entry point (beat), not scheduling logic.

**Scheduler startup (CLI)**
- Start the scheduler from the command line with a custom scheduler class whose run fails immediately; expect: exit code 1 (failure is surfaced to the shell, not swallowed), and the output begins with the scheduler banner.
- Same start; expect: the output includes the effective configuration summary, so operators can see what the scheduler started with.

**Quiet mode**
- Start the scheduler with the global quiet flag and the same failing scheduler; expect: exit code 1 still (errors are never hidden by quiet), but no banner and no configuration summary in the output.
