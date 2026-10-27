### `t/integration/test_worker.py`

**Startup and unreachable database**
- Start a worker while the database/broker is unreachable and startup-retry is disabled; expect: the worker exits with status 1 and prints a clear message that retry on startup is disabled and it is shutting down (fail fast, no silent hang).

**Framework integration (Django mode)**
- Build a worker through the Django integration with a single-process pool; expect: worker configuration (pool class) is readable, is a real class, and no recursion error or "str has no __module__" error appears in the logs.
- Subclass the app class without customising its task class, with Django configured; expect: tasks still get the Django-aware base (including run-after-commit enqueue helpers).
- Two Django-enabled apps in one process; the loader's module import for app 2 fires; expect: only app 2's integration validates models, app 1 untouched.
- Start worker 2, then worker 1; expect: each integration installs only for its own app and holds a reference to its own worker, never the other's.
- After tearing both apps down; expect: no signal receivers remain connected to either integration (no leaks between apps or tests).

**Worker sizing**
- Start a real multi-process worker with concurrency "auto"; expect: max concurrency reported by inspection equals the CPU budget, and the log states that "auto" resolved to that number for that pool type.
- While waiting for the worker to come up, if it dies; expect: the test fails with the worker log (startup crash is surfaced, not swallowed).
- On shutdown, terminate gracefully; if it does not exit within the timeout, kill it (no orphaned worker processes).

**Control channel resilience**
- Ping a running worker; expect: it replies "pong".
- Send it 5 broadcast control commands that raise errors (no reply requested); expect: worker survives, the control channel resets, and a ping still answers "pong".
- After those errors, enqueue add(4, 4) and wait for the result; expect: 8 within the timeout (job processing unaffected by control-channel failures).
- Ping again after the job; expect: still "pong".
