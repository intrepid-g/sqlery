### `t/unit/backends/test_couchdb.py`

Vendor result-store wiring (CouchDB); the portable guarantees are result storage, idempotent cleanup, conflict handling and error preservation.

**Configuration**
- Start the result store when its client library is not installed; expect a clear configuration error, not an obscure crash later.
- Ask for the store a second time when a connection already exists; expect the existing connection reused, no new connection opened.
- Configure the store with a URL scheme; expect the matching store implementation chosen and the URL passed through unchanged.
- Configure with a URL carrying user, password, host, port and container name; expect each part parsed into the right setting.

**Reading and writing results**
- Read a result that exists; expect its stored value returned.
- Read a result that was never stored (or expired); expect "nothing" returned, no exception.
- Save a result using a text key, then using a bytes key; expect the same record written under the same text id.
- Save a result when a record with that id already exists (write conflict); expect it to fetch the existing record, update its value, save again (two save attempts), and not raise.
- Delete a result; expect success with nothing returned.

**Forgetting results**
- Forget a result that was expired, already forgotten or never stored; expect a silent no-op, no "not found" error.
- Forget the same result twice; expect the second call to be a silent no-op.
- Store a success result, then forget it; expect state back to pending (unknown).

**Job lifecycle states round trip**
- Store a successful result; expect state success and the same result value back.
- Mark a job as started; expect state started.
- Mark a job as revoked (cancelled); expect state revoked.
- Mark a job as retrying with an exception and traceback; expect state retry, the original exception object (same type) retrievable, and the traceback text identical.
- Mark a job as failed with an exception and traceback; expect state failed, the original exception object (same type) retrievable, and the traceback text identical.
