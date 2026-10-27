### `t/unit/app/test_app.py`

This file covers the Celery "app" object: job registration, configuration, enqueueing (send_task), delayed delivery, expiry, time zones, pickling, thread/fork behaviour. Portable guarantees only.

**Job registration and naming**
- Register two different functions that end up with the same job name (closures, same-named methods in two classes, two classes registered by name); expect: a duplicate-name warning that names the job and points at the caller's code, not library internals.
- Register a third callable under an already-duplicated name; expect: no second warning (warn once per name).
- Register a class-based job over an existing function-based job with the same name; expect: warning says the old one was "replaced" and names the old function.
- Register the same function twice (re-imported module, bound jobs, validated-argument jobs); expect: same job object returned, no warning.
- Register jobs with distinct explicit names, or only differing function names; expect: no warning.
- Register a class-based job, then a function under its name (no callable recorded to compare); expect: no warning.
- Job defined in a script run as main; expect: name is prefixed with the app name.
- Pass extra positional args to the job decorator; expect: TypeError. Pass a non-callable first arg; expect: TypeError.
- Job decorator with a filter hook; expect: hook sees the created job.
- Job not marked shared; expect: not registered for other apps.
- Shared job declared once; expect: bound to whichever app is current, and re-binds when another app becomes current.
- Apply the shared-job decorator twice to the same function; expect: TypeError "more than once".
- Shared job wrapping an app job, run eagerly; expect: result returned correctly.
- Job with type hints; expect: hints are readable. Job whose annotations reference undefined (type-check-only) names on deferred-annotation Python; expect: registration succeeds, job runs, annotations kept as strings.
- Per-job annotations configured by name (e.g. wrap its call); expect: wrapper applied, and re-annotating is safe/idempotent.
- With auto-finalize disabled: calling a job or reading a built-in job from the registry before finalize; expect: RuntimeError. After explicit finalize; expect: works.
- Custom registry class supplied; expect: the app uses it. Custom registry object passed in; expect: shared by identity.
- Enqueue a child job from inside a running parent with add-to-parent; expect: child appears in parent's children.
- Windows/exec-style (spawned) worker mode: job decorator still returns a lazy proxy resolving to the real registered job, accepts explicit lazy flag, and env var set after import is honoured.

**Typed-argument (validated) jobs**
- Jobs with no args, simple args, optional, dict, list, nested model, default-model arguments; expect: called with validated values, results serialised.
- Non-strict mode accepts coercible input (1.0 for int, ISO duration string); strict mode rejects it; expect: ValueError and the job body never runs.
- Task name passed to validators as context; extra user context and dump options (exclude-unset, by-alias) honoured.
- Caller passes model objects to delay/apply_async/signature; expect: converted to plain dicts before sending (args/kwargs container type kept, untouched args keep identity). Eager mode round-trips correctly.
- Model passed to a job without validation, or with pickle serializer, or with its own JSON hook; expect: left alone.
- Model that cannot be dumped faithfully (unserialisable field, masked secret, excluded non-default field, subclass with extras or forbidding extras, strict-mode-incompatible fields, validator failing on caller side in eager mode); expect: EncodeError at enqueue time, job never runs.
- Model validator that only fails on the worker; expect: sent as-is from the caller.
- Model for params not validated as models (unannotated, union, *args, **kwargs, unresolvable string annotation); expect: left alone.
- Bound jobs and string annotations map args to the right parameters.
- args not a list/tuple or kwargs not a mapping; expect: TypeError.
- Validation library not installed; expect: ImproperlyConfigured with install hint.

**Configuration**
- Defaults added lazily; expect: app not "configured" until config is read; later added defaults still apply; defaults not carried in pickles.
- Constructor kwargs, broker/backend args, include list, SSL option dicts; expect: readable via config before and after configuring.
- Setting/updating/setdefault before configure; expect: stays pending, preconf survives pickling.
- Old-style (UPPERCASE) and new-style setting names; expect: both readable and equivalent; mixing old and new names in one config; expect: ImproperlyConfigured naming the conflicting setting (except when the same value is given under both).
- Namespaced settings (upper or lower case); expect: prefix stripped. Runtime changes override loaded config.
- Config from object, dotted-name, env var, command line (typed values like (int), (dict)); expect: values applied. Lazy by default, eager with force.
- Missing env var: silent returns False, not silent raises ImproperlyConfigured. Unknown module: silent swallows, otherwise ImportError. Existing module but missing attribute; expect: ModuleNotFoundError naming both.
- The silent flag must survive lazy load, eager load, pickling, legacy v1 pickling, and legacy payloads without the flag. After a silent failed reload, new defaults can still be added.
- Default broker transport options present; user-supplied replace them.
- On-configure hook still called for legacy subclasses.

**Enqueue (send_task)**
- Send with a provided connection and router; expect: message published once with that connection.
- Job-level serializer used when sending by name; explicit serializer overrides; unknown job name uses app defaults; registry without .get() or raising KeyError tolerated.
- Job-level time limit, soft limit, expiry used by default; explicit values override; explicit None clears them; no "multiple values" TypeError.
- Job-level exec options skipped if not a bound method.
- Expiry as ISO string, datetime, naive (treated as UTC); expect: no TypeError, correct seconds across DST spring-forward and fall-back, expired-already during fall-back gives 0.
- Task-sent event published when requested.
- Delay (countdown/ETA) with native delayed delivery: countdown, datetime ETA, string ETA, DST-correct; expect: routed via delayed exchange with encoded delay. No ETA/countdown, negative countdown, ETA in the past or equal to now (even during fall-back, countdown negative), or no quorum queues; expect: normal immediate publish, no detection call when no delay.
- Delayed delivery on a direct exchange; expect: warning that the job may block a worker until ETA.
- Connection/producer pool exhausted (acquire timeout/limit); expect: OperationalError mentioning the pool limit; timeout setting passed through (None means wait forever, default None). Non-pooled acquire bypasses pool.

**Scheduled (periodic) jobs**
- Add periodic job before configure; expect: held pending, applied on configure; after configure, applied immediately.
- Re-add with same explicit name; expect: replaced, one entry. Re-add unnamed identical; expect: replaced and a warning that it was shadowed, advising a name.

**Time and identity**
- app.now() is UTC-aware; with a timezone setting it is converted; timezone setting overrides enable_utc; uses_utc_timezone reflects settings; no timezone and not UTC falls back to local, and to a fallback local zone if detection fails.
- App id (oid) is the same process-wide, including across threads; per-thread id differs between threads.
- Result backend: same instance within a thread, distinct per thread, shared if thread-safe is on; accepts a class.

**Process lifecycle and misc**
- After fork: pool dropped and after-fork signal sent; exceptions in after-fork logged, not raised; registered once; works without multiprocessing.
- Pickled app is small (<2KB), keeps config changes, results unpickle onto current app; weak registry of active apps drops garbage-collected apps.
- Failover strategy: string kept, None defaults to cycle, callable honoured. Broker URL parsed into host/user/vhost/port.
- Boolean setting parser: false/no/0, true/yes/1; anything else raises TypeError.
- Trace helpers toggle; python implementation detection (CPython, PyPy, Jython); set-as-current, set_default, security setup, worker/beat/start entry points, bug report text, canvas/signature binding, current job lookup: simple wiring checks.

---
_Generated by [Claude Code](https://claude.ai/code)_
