### `t/unit/backends/test_arangodb.py`

Mostly vendor wiring (ArangoDB result backend). Portable guarantee: results are stored, fetched singly or in bulk, deleted idempotently, and expired by age; bad configuration fails loudly at start-up.

**Configuration and start-up**
- Result store driver library not installed; start the backend; expect a clear configuration error, not a later crash.
- Backend settings given as an invalid empty value; expect a configuration error.
- Backend settings explicitly None; expect start-up succeeds with defaults.
- No URL given; expect default database and collection names used.
- URL naming a database and collection; expect exactly those used.
- Explicit settings (host, port as a string, user, password, database, collection, https, verify on); expect all honoured, port coerced to a number, full URL composed correctly.
- Backend URL scheme selects this backend and the URL is passed through unchanged.
- Everything configured via URL only; expect host, port, credentials, database, collection parsed, with defaults http and verify off.
- Connection is lazy: an existing connection is reused without reconnecting; with none, exactly one new connection is made.

**Storing and reading results**
- Get with no task id; expect None and no query to the store.
- Get an id that was never stored; expect None.
- Set a result for a key; expect it upserted (inserted if new, overwritten if present) and nothing returned.
- Bulk get with no keys; expect empty and no query.
- Bulk get of keys with no stored results; expect empty.
- Bulk get of two stored keys; expect both values returned in key order.

**Deleting**
- Delete with no id; expect no-op, no query.
- Delete a stored result; expect it removed.
- Delete a missing result; expect silent no-op, no error.

**Expiry and cleanup**
- Cleanup with expiry unset (None); expect nothing removed.
- Cleanup with expiry 0; expect nothing removed.
- Cleanup with expiry 1 day; expect only results completed before (now minus 1 day) removed, newer kept.
