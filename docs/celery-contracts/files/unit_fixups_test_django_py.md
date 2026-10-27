### `t/unit/fixups/test_django.py`

Portable guarantee: the queue integrates with Django without leaking DB connections across jobs or forked workers, and never misfires across multiple apps.

**Activation**
- Django not installed and settings var empty: queue starts normally, no integration. Settings var set but Django missing: warn, still start without it.
- Django installed and settings var set: integration activates.
- No default app yet: integration registers its app as default.
- Worker-side integration is created lazily once and reused.
- Startup (module import) triggers model/system validation.
- Autodiscovery of job modules returns every installed Django app.

**Install**
- Install registers startup hooks, replaces the clock with Django's, and puts the project directory first on the path.
- Default job class gains after-commit enqueue methods (`delay_on_commit`, `apply_async_on_commit`).
- User-supplied job class is respected whether set on the app, as a class attribute, or inherited; merely subclassing the app is not custom, so the default Django class still applies.
- Clock: UTC request uses the plain clock; non-UTC uses Django local time.

**Multiple apps and worker attachment**
- Two apps, event for app B: only B's integration reacts, for both import and worker-start events.
- Worker of another app: ignored (debug logged), no worker integration created.
- Worker whose app is a lazy proxy resolves to the real app and attaches; proxy of another app is ignored; the integration's own app may be a proxy too.
- Worker-start event with no app attribute: attach anyway. With no sender at all: warn.
- Each integration instance has a unique, random hook id (not memory-address based, as addresses get recycled).
- Creating the worker integration must not construct a new worker (regression: infinite recursion).

**Connection hygiene around jobs**
- Before a job runs, close DB connections; before/after an eagerly (inline) run, leave them open.
- After a job: close DB connections and caches, unless eager.
- Connection reuse limit unset: close after every job. Limit N: keep open and count until exceeded, then close and reset counter.
- Closing always hard-closes every connection; never relies on a "close if unusable" check.
- One connection already closed (DB error on close): the rest still close, no failure. Unexpected error on close: propagates.
- Older Django lacking the `initialized_only` argument: fall back to closing all connections.
- Connection pools: close the pool only if configured for it and the worker is prefork; skip for thread pools and non-pooled connections; "pool already closed" error is swallowed.
- Cache close failure is swallowed.

**Worker process start (fork)**
- New child process closes inherited DB file descriptors (tolerating interface errors), closes cache and DB, and re-validates models when forked by multiprocessing.
- Prefork detection works for pool class or name; no worker means not prefork.

**Validation**
- Model validation runs framework setup then system checks; skip-checks env var runs setup but skips checks.
- Setup initializes the framework once via its own setup call.
