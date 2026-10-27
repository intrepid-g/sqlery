### `t/unit/backends/test_elasticsearch.py`

Portable guarantee: a result store (Elasticsearch here) keeps one result per job, never lets a late or racing write clobber a finished result, survives concurrent writers, and treats "missing" as normal. Vendor client wiring is not portable; only the guarantees are listed.

**Setup and configuration**
- Result store library not installed; expect: a clear configuration error at startup, not a crash later.
- Result store URL with scheme `elasticsearch://`; expect: the matching result backend is selected.
- URL gives index, doc type, host, port; expect: all picked up. URL `elasticsearch:///` with nothing; expect: defaults (index `celery`, no doc type, http, localhost, 9200).
- URL `elasticsearch+https://user:pass@host`; expect: https, credentials passed to the client. No credentials; expect: no auth passed.
- Config sets max retries, timeout, retry-on-timeout; expect: all passed to the client unchanged.
- Backend created but never used; expect: no connection opened until first use (lazy), and opened only once.

**Reading results**
- Job result stored, then fetched by id; expect: the stored result, looked up in the configured index (doc type sent only if configured).
- Fetch an id that was never stored or has expired (store raises not-found); expect: None, no error.
- Fetch where the store answers "found: false" without raising; expect: None.
- Fetch many ids at once; expect: results returned in the same order as the ids asked.

**Deleting / forgetting**
- Delete a stored result; expect: it is removed, nothing returned; doc type sent only if configured.
- Forget a result that expired, was never stored, or is already gone; expect: silent no-op, no not-found error.
- Forget the same job twice; expect: second call is a no-op.

**Writing results and write conflicts**
- Write a result for a new job; expect: create-only write (never silently replaces), with a UTC timestamp attached.
- Job id arrives as bytes and body keys as bytes; expect: both converted to text before storing.
- Write collides with an existing result (another writer got there first); expect: the existing record is read and updated using its version marker (optimistic concurrency), not blindly overwritten.
- Existing record is a RETRY and a new result arrives; expect: the new result replaces it.
- Existing record is SUCCESS and a late write arrives; expect: existing SUCCESS kept, no update issued.
- Existing record is FAILURE (finished) and a late RETRY arrives; expect: finished state kept, no update issued (a finished job never goes back to a non-final state).
- Write has no state attached (raw set) over an existing FAILURE; expect: no protection possible, the update goes through.
- Update is rejected because the record changed meanwhile (version mismatch, "noop"), with retry-on-backend-error enabled; expect: wait once, re-read the new version, update again with the new version, finish with SUCCESS stored.
- Conflicting record is deleted between the failed create and the re-read (not-found or "found: false"); expect: create is retried, no update, no waiting.
- Conflicting record exists but is corrupted (no result field); expect: treated as overwritable, updated with its version, no waiting.

**Failure handling / retry classification**
- Retry-safe errors: write conflict, connection error, transport error (e.g. too many requests); expect: judged safe to retry.
- Not retry-safe: generic exceptions, base exceptions, not-found; expect: not retried, surfaced.

**Result encoding**
- Result metadata for a dict, for None, and for a failed job's exception (type, message, module, traceback), saved as structured JSON; expect: encode returns it as is, and encode then decode round-trips to the identical record.
- Decode of an empty/None result; expect: returned unchanged.
- Decode of something that is not a record; expect: handed to the generic decoder and its answer returned.
