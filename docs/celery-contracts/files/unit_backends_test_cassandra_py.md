### `t/unit/backends/test_cassandra.py`

Portable guarantees for a vendor result store (Cassandra); client wiring is Celery-specific and omitted.

**Configuration**
- Start the result store with its driver library missing; expect a clear configuration error, not a crash later.
- Start with no servers and no cloud bundle configured; expect a configuration error.
- Start with both servers and a cloud bundle configured (ambiguous); expect a configuration error.
- Configure an unknown consistency level; expect fallback to a safe default (local quorum), and a known one is used.
- Configure an unknown auth provider; expect a configuration error. A valid provider with its kwargs is accepted.
- Configure with a cloud bundle; expect the connection to be built from the bundle only, with no contact points.
- Omit the port; expect the default (9042). An explicit port wins.
- Serialize and restore (pickle) the store object; expect a working copy.
- Render the store's URI with and without the password; expect no error.

**Storing and reading results**
- Store a success result then read it; expect status success and the same value back.
- Read a job id that has no stored row; expect status pending, not an error.
- With compression on, store a result; expect result, traceback and children all stored as bytes, and decoding returns the original.
- With compression on and either JSON or pickle serialization, store a large repetitive result then read it; expect it stored compressed (magic marker) and read back equal.
- Result written before compression was enabled (uncompressed); after enabling compression, a reader still reads it correctly.

**Connection failures**
- Connecting times out while storing a result; expect the timeout to propagate, and the cluster and session cleared so the next attempt reconnects fresh.
- Table creation reports "already exists"; expect it treated as success and the session usable.
- Table creation fails for another reason (e.g. no CREATE permission); expect the error raised, but the freshly opened session kept (not shut down), table marked not created, and later calls reuse it with no second connection (no leaked clusters).
- Same failure when a reader already opened the session; expect the existing session and cluster untouched.

**Concurrency and lifecycle**
- Open a session twice; expect the same session reused, not rebuilt.
- While one thread is creating the table, a second thread tries to write; expect the second to wait and not use the session until the table exists (order: CREATE, then INSERT).
- A read opens the session without creating the table; the first later write issues CREATE once, then writes; further writes only INSERT.
