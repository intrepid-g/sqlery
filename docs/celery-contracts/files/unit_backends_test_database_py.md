### `t/unit/backends/test_database.py`

Portable guarantee: a database-backed result store records job outcomes durably, survives transient database failures, tolerates old or corrupt rows, and never lets a late duplicate overwrite a success.

**Transient database failures and retries**
- Saving a job result fails twice with a database error, third try works; expect: result saved, 2 waits between tries.
- Reading a result fails once, then works; expect: the stored SUCCESS is returned, 1 wait.
- Saving a group/batch result fails once; expect: retried and saved.
- Forgetting (deleting) a result fails once; expect: retried and removed.
- Expired-result cleanup fails once; expect: retried and completes.
- "Does a result exist" check fails once; expect: retried, answers true.
- Opening the session/creating tables fails once; expect: retried and succeeds.
- Database keeps failing with max retries = 2; expect: 3 attempts, 2 waits, then a clear "store failed" error.
- Every retryable failure discards the pooled connections/engine so the next try gets a fresh connection (once per failure).
- A non-database error (bad value) during save; expect: no retry, no wait, error raised at once.
- Retry enabled but max retries = 0; expect: original exception raised, no wait.
- Retry defaults: on, 3 retries. Settings left empty fall back to those defaults. Explicit "off / 0" is honoured, also under the legacy setting names.
- Which errors count as retryable: database error, invalid request, stale data, lost connection (interface error) yes; arbitrary runtime error no.

**Configuration**
- No database URL configured; expect: configuration error at start.
- Connection pool defaults to health-check-before-use and recycle after 3600s; explicit settings and constructor options override per key.
- Table names and schema names are configurable per table (jobs, groups).
- Tables are created at setup by default; creation can be switched off.
- Engine hook that is not callable; expect: configuration error.
- Engine hook comes from config, from constructor argument (which wins over config), or from a dotted path.
- Engine hook runs once per new engine (forked or not), again after the engine is invalidated, not for a cached engine, and before tables are created; a hook can register a per-connection event that fires on real use.
- Pool-specific options (pool size, overflow) are dropped when the engine uses no pooling.

**Job states and results**
- Unknown job id; expect: state PENDING, result and traceback empty, id echoed, "exists" is false.
- Job marked done or failed; expect: "exists" becomes true.
- Mark done with 42; expect: PENDING before, SUCCESS and 42 after.
- Complex objects (custom classes) round-trip intact.
- Result bytes in the database follow the configured serializer (JSON stays JSON, not an implicit pickle), for job results and group results.
- Old rows written as pickle are still readable after switching to JSON, with a warning logged.
- Bytes that are neither valid JSON nor a pickle; expect: error raised, never blindly unpickled.
- Started and revoked states are recorded and readable.
- Retry and failure keep the exception object and the traceback text.
- Late duplicate writes after SUCCESS (started, failure, another success) because a lost worker's job ran again; expect: original SUCCESS and result stay.
- Before success, started, then retry, then failure, then done; expect: every transition saved, final SUCCESS wins.
- Second run starts just as the first saves SUCCESS, row locks available; expect: SUCCESS is kept.
- Same race on a database without row locks (SQLite); expect: the second run's state may land, and a later done still sets the final result.
- Forget a result, even one written twice; expect: result reads as empty.
- Child jobs are stored and returned in order (both serializers); none when there are none; a job object without a children attribute is tolerated.
- Group: save, restore equals original, delete, restore returns nothing; restoring an unknown id returns nothing.
- Cleanup removes expired job and group results.
- Expiry is computed in UTC regardless of the configured timezone (24h expiry: 20h-old kept, 30h-old removed).
- Session helper: closes the session on success; on error rolls back, closes, and re-raises the error.
- Completion timestamps are evaluated per row at insert/update (not once at import) and are indexed; id columns are big integers on MSSQL.
- Backend object can be pickled/copied; process cleanup is harmless.

**Schema drift and concurrent setup**
- Existing table lacks newer columns (children); expect: added automatically.
- Adding the column fails (permission denied); expect: warning logged, no crash, reads still work with children empty.
- Inspecting the schema throws; expect: ignored with a warning. Table does not exist; expect: no migration attempted.
- Query fails because a newer column is missing; expect: retried without that column. Unrelated database error; expect: raised.
- Many workers create tables at once and collide; expect: bounded retries (limit + 1 attempts), then the error is raised, never an endless loop.

**Sessions, forking, connection reuse**
- After a process fork, the manager is flagged and builds a fresh engine; engines are cached per URL.
- Short-lived sessions give a new session every call; long-lived mode reuses one.
- Invalidate disposes the cached engine and session; unknown URL is a no-op; engine-only cache also fine.

**Extended results (args, kwargs, name, worker, queue, retries, stamps)**
- Storing with extended mode saves and returns args, kwargs, job name, retry count, worker host and queue, for both serializers, also when the result is None.
- Job rerun on another worker after SUCCESS; expect: extended fields unchanged.
- Stamps (tags attached to a job) round-trip flattened into the result, with the header list; the raw "stamps" key never leaks.
- No stamps; expect: neither stamps nor header list in result.
- Header listed but no value; expect: omitted. Stamps without headers or headers without stamps; expect: still read sensibly.
- Corrupt stamps (non-dict or undecodable bytes); expect: ignored, result still readable, no raw bytes leaked.
- Extended mode off; expect: no stamps column or fields at all.
- Stamps column missing in an old database; expect: queries fall back gracefully; a failure while reading stamps yields empty stamps.
- Stamped job signature stored and fetched via a result handle keeps result, state and stamps.

---

_Generated by [Claude Code](https://claude.ai/code)_
