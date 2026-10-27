### `t/unit/backends/test_s3.py`

Portable guarantee: a result store (S3 here) keeps results retrievable by job id, reports missing ones cleanly, deletes on request, and surfaces storage errors instead of hiding them.

**Configuration and startup**
- Start the result store with no credentials in config and none discoverable in the environment; expect a configuration error naming the missing credentials.
- Start it with no credentials in config but some discoverable in the environment; expect the environment lookup to happen exactly once.
- Start it with credentials in config; expect no environment lookup.
- Start it with no bucket name; expect a configuration error naming the missing bucket.
- Start it with and without a region; expect the connection to be built with exactly the configured credentials and region (None when unset).
- Start it with and without a custom endpoint URL; expect the storage resource to be built against that endpoint (None when unset).

**Store and retrieve results**
- Store a result for a job id, then read it back; expect the same value. Works whether the id is text or bytes.
- Store a job result with state STARTED using the pickle serializer, then fetch the result; expect the original value back.
- Read a job id that was never stored; expect "nothing" (None), not an error.

**Delete**
- Store a result, confirm it is readable, delete it; expect a later read to return nothing. Works for text and bytes ids.

**Storage failures**
- Read when the store denies permission (403); expect the error to propagate to the caller, not be swallowed as "missing".
- Write to a bucket that does not exist; expect an error saying the bucket does not exist.
