### `t/unit/backends/test_filesystem.py`

Portable guarantee: results stored as files in a directory are durable, retrievable, optionally compressed, expire by age, and bad configuration fails loudly.

**Configuration**
- Create a result store with no path; expect: a clear configuration error.
- Create a store from a valid directory URL; expect: it uses that directory.
- Create a store from a nonexistent directory, a wrong URL scheme, or no URL; expect: a distinct, meaningful configuration error for each.
- Use a URL with a `localhost` host; expect: host ignored, same directory used.

**State and results**
- Ask for the state of a job id never seen; expect: pending (not an error).
- Mark a job done; expect: exactly one record stored.
- Mark a job done, then read its state; expect: success.
- Store a structured result; expect: reading it back returns an equal value.
- Store results for 3 jobs, fetch them all at once; expect: each result matches its job.
- Mark a job done, then forget it; expect: its record is deleted, store empty.
- Serialize (pickle) the result store; expect: it round-trips.

**Compression**
- Enable compression, store a large result; expect: stored bytes carry the compressed marker and read back equal.
- Store a result uncompressed, later enable compression; expect: old result still readable.
- Store a result compressed, later disable compression; expect: still readable.
- Use a binary serializer with non-text bytes plus compression; expect: round trip is exact and on-disk data is compressed.
- Store a binary-serializer payload uncompressed, read with compression on; expect: not mistaken for compressed, read back equal.

**Expiry cleanup**
- Store 10 old and 10 newer results; run cleanup with expiry 0; expect: nothing removed (zero means never expire).
- Run cleanup with a nonzero expiry between the two batches; expect: the 10 old results removed (by file modification time), the 10 newer kept.
