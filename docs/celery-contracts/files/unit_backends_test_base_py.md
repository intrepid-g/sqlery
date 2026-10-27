### `t/unit/backends/test_base.py`

Celery's result-backend contract (where job outcomes are stored and read). Mapped to sqlery: result backend = the job row's status/result/error columns; chord = dependent job waiting on a group; errback = on-failure callback job.

**Stored result record**
- Job succeeds with a value; expect: record shows done, the value, no traceback.
- Job succeeds returning nothing; expect: done, result is null (not missing, not an error).
- With extended results on, expect the record to also carry job name, args, kwargs and the queue it ran from; args/kwargs can optionally be stored encoded.
- Completion timestamp can be returned as text or as a real datetime, as requested.
- Job request lacks optional attributes (no stamps, etc.); expect: still stored, no crash.
- Job carries tags/stamps: only the declared ones are copied into the record; undeclared extras are ignored; if none declared, none copied; a stamp named status or result must never overwrite the real status/result.
- Parent job id and group id are stored with the record, under every serializer (json, pickle, yaml, msgpack).
- Accepted content types: default from config; a result-specific setting overrides it; an explicit argument overrides both.
- Backend plugin that names its hook parameters differently; expect: values are passed by position and still stored, read, forgotten correctly.

**Lifecycle and keys**
- Mark job done, read back; expect: result and state done. Forget it; expect: state pending.
- Read a job that never existed; expect: pending, result null, no error.
- Job already done, then a late failure write arrives (race); expect: done is kept, failure ignored.
- "Result exists" check; expect: true for done, failed, retrying; false for unknown, pending, or forgotten.
- Reload of a job or group result bypasses the cache; "is cached" reflects the cache only.
- Key for a null id; expect: ValueError. A UUID object and its string form yield the same key.
- Global key prefix configured (with or without trailing separator); expect: applied to job, group and dependency keys; absent prefix gives plain keys; stripping a prefix is idempotent.
- Base class abstract operations (get, set, incr, delete, mget, forget, add-to-group) raise not-implemented; cleanup is a harmless no-op.

**Errors are stored and come back faithfully**
- Job raises; expect: stored error restores to the same exception type with the same message (json and pickle), including nested classes and non-ASCII text.
- Exception whose arguments cannot be serialized; expect: stored safely, restores as an exception, no crash.
- Exception class that cannot be pickled; expect: replaced by the nearest picklable parent; if none exists, wrapped, and restores with the original class name and module.
- BaseException subclasses and async cancellation are stored and restored with their args.
- No error stored (null); expect: null back.
- Stored error naming something that is not an exception (os.system, builtins.object); expect: SecurityError, never executed.
- Stored error whose class is gone, or whose constructor takes different arguments; expect: readable fallback exception, no crash.
- Fail from the current exception stack; expect: failure recorded with traceback, and the traceback does not retain local variables.

**On-failure callbacks**
- Job fails with a callback attached; expect: callback runs (inline when eager, otherwise queued as a group carrying parent and root ids).
- Old-style callback (receives job id) with preset args or kwargs; expect: failed job id prepended, works eager or not.
- New-style callback (request, exception, traceback, extras); expect: all passed, preset args kept.
- Callback with invalid signature; expect: skipped and logged, other callbacks still run.
- A callback raises; expect: caught and logged, later callbacks still run, nothing propagates.
- Class-based tasks and not-registered task names are usable as callbacks.
- Success, failure or revoke of a job that belongs to a dependency group; expect: group tracker notified with that state.
- Failure of a job whose chain contains a dependent group; expect: the dependent body is marked failed too.

**Dependent (group-then-body) failures**
- A group member fails; expect: body marked failed with its id; if the body has no id, one is generated and saved.
- Jobs chained after the failed body also become failed, including after a group body; the exception is resolved from the current error if not passed.
- The failure callback itself crashes; expect: that crash is what is recorded as the failure of the body and chained jobs.
- Chained dependent member fails twice; expect: counted once. Dependent with no body; expect: skipped. Plain chain element; expect: no state lookup (so it works with results disabled).
- Group body fails; expect order: every member's failure stored, group record failed, then outstanding members revoked (revoke keeps already-finished results, so stored first).
- Each group member runs its own callbacks plus the group's, de-duplicated; callbacks given as one item or a list; nested dependents use their body's callbacks; a body-less dependent does not stop the others.
- Error while handling the failure; expect: logged ("Failed to handle chord error"), no crash; freeze failure falls back to single-job handling.
- Handler is chosen by body type (group vs single); the job's own backend is used, falling back to this one.
- Wrapped dependency error keeps the original as cause; a non-exception cause is ignored; message intact.
- Synthetic request for callbacks: id and callbacks given, name defaults to "unknown", extras kept.

**Dependency completion (last part returns)**
- Last member of group returns; expect: results joined using configured join timeout, group record deleted, no expiry left behind.
- Join raises an internal error; expect: body failed with an error containing the message. Join fails because a dependency failed; expect: error names that job ("Dependency X raised").
- Member returns with no group id; expect: nothing happens. Group cannot be restored or is empty; expect: body failed.
- Backend without atomic counter; expect: falls back to polling unlock job; part-return is a no-op.
- Follow-up unlock job queue: explicit body queue > router > body task default > app default; exchange, routing key and headers are forwarded; unknown queue or queue without exchange adds no extra routing options; stamps do not leak into headers; works across apps.
- Results disabled; expect: dependent groups and chains containing them raise not-implemented, plain-chain failure handling still works.

**Groups**
- Save, restore, delete a group result (json and pickle); expect: equal on restore, null after delete; unknown group gives null.
- Group and job result objects serialize to tuples under json, native objects under pickle.
- Children of a job: null when none, value when present.

**Waiting and polling**
- Wait on pending job with timeout; expect: TimeoutError, interval callback fired; done job returns.
- Timeout 0; expect: immediate TimeoutError, no sleep, never waits forever. Timeout shorter than poll interval; expect: total sleep equals timeout, never overshoots.
- Get many results; expect: all returned whatever the store returns (list or dict), as they complete, message callback per result, repeat call served from cache.
- Get many with a job never finishing; expect: TimeoutError. Timeout 0 still returns what is already done. Last result arriving exactly at the deadline; expect: success.
- Ready-state filter and iteration cap respected; retrying is not "ready", failure and success are.

**Backend outages**
- Read fails, retry disabled; expect: original error immediately, no sleep. Retry enabled but error not marked safe; expect: not retried.
- Retry enabled, safe error then success; expect: one sleep, value returned, retry hook called once; a crashing hook does not stop the retry.
- Retries exhausted on read; expect: BackendGetMetaError carrying the job id. On write; expect: BackendStoreError carrying job id and state.
- Same rules for writes: no retry when disabled, retry when enabled and safe, unsafe never retried.

**Compression**
- Off by default; payload plain.
- gzip, zlib, bzip2 round trip, marked with a magic prefix, smaller for repetitive data; exceptions round trip too.
- Reader with compression on reads old uncompressed results; reader with compression off reads compressed ones; switching mid-life loses nothing.
- Binary serializers (pickle, msgpack) with arbitrary bytes round trip; an uncompressed binary payload is never mistaken for compressed.
- Backend that cannot store bytes; expect: warning, compression disabled. Unknown method; expect: ImproperlyConfigured. Non-payload types and memoryviews pass through safely.

**Misc**
- Disabled backend: storing is a no-op, reading state raises, URI is "disabled://".
- Backend URI hides the password by default and shows it only when asked.
- Thread-safety flag follows config.
- Warning if failure-expiry is configured but the backend cannot honour it; no warning when unset.

---

_Generated by [Claude Code](https://claude.ai/code)_
