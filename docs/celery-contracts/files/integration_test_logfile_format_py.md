### `t/integration/test_logfile_format.py`

Portable guarantee: a worker's log file path template is expanded exactly once, escaped percent signs survive as literal characters, and log lines really reach the file. Only the log-file naming part is portable; the rest is Celery wiring.

**Single worker, log file name template**
- Start a worker with log file `%%n-%n.log`; log one line; expect: file named `%n-worker.log` exists (escaped `%%` becomes a literal `%`, `%n` becomes the worker name) and contains the line.
- Start a worker with log file `%%x.log`; expect: file named `%x.log` (an escaped placeholder is not expanded) and it contains the line.

**Several workers started together (template passes through two expansion layers)**
- Launch a multi-worker node with log file `%%%%n-%n.log`; expect: the worker writes to `%n-worker.log`, with no double-expansion and no error.
- Launch with `50%%%%d.log`; expect: file `50%d.log` (a literal percent followed by a letter that looks like a placeholder is kept as is).
- Launch with `%%%%x.log`; expect: file `%x.log`.
- Launch with `%%%%i-%i.log`; expect: file `%i-0.log` (the real placeholder gives the worker index 0, the escaped one stays literal).

**Robustness**
- Every case runs in a fresh isolated process with real file handlers; expect: clean exit (no crash on odd percent patterns) within 30 seconds, and the log line flushed to disk after logging shutdown.
