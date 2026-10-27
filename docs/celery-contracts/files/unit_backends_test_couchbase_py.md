### `t/unit/backends/test_couchbase.py`

Couchbase is a vendor result store; most of the file is client wiring, but these portable result-store guarantees are covered.

**Configuration and startup**
- Start the result store when its client library is not installed; expect a clear configuration error, not a crash later.
- Configure the store with an empty settings list; expect a configuration error.
- Configure with settings set to None; expect it to start with defaults.
- Configure with bucket, host list, username, password and port given as a string; expect all kept, port converted to an integer.
- Configure from a URL with credentials, host, port and bucket; expect each part parsed correctly.
- Resolve a store by URL scheme; expect the right store type and the URL returned unchanged.
- Ask for a connection when one already exists; expect it reused, no new connection opened.

**Result retrieval**
- Fetch a stored result by key; expect its content back.
- Fetch a result that was never stored or has expired; expect "absent" (None), not an error.
- Ask for the state of a job whose result was never stored or has expired; expect PENDING, not an exception.
- Fetch hits a non-"missing" store error (e.g. timeout); expect the error reaches the caller and is not mistaken for a pending job.

**Batch retrieval**
- Fetch several results at once; expect a plain key-to-value map.
- Batch fetch must request per-key errors so one failed key does not discard the other results.
- Batch fetch where one key is missing; expect that key mapped to absent, others returned.
- Batch fetch where one key fails with a non-"missing" error (timeout); expect the error propagates.
- Wait for a group where one job is done and one has no result yet; expect the done result returned, no crash, no hang on the missing one.

**Storing and expiry**
- Store a result with no expiry configured; expect success, stored without a time limit.
- Store a result with an expiry of 30 seconds; expect the expiry kept at 30 and the store succeeds.
- Expiry unspecified; expect the global result-expiry default used.
- Expiry given as an integer; expect it kept as is.
- Expiry given as a duration (1 minute); expect it converted to 60 seconds.

**Deletion**
- Delete a stored result; expect it removed and nothing returned.
- Delete a result that is already expired or never existed; expect a silent no-op, not an error.
