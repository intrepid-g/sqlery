### `t/unit/app/test_backends.py`

**Result store selection (by URL or name)**
- Configure the result store with a URL alias (e.g. an in-memory cache URL); expect: the matching store type is resolved and can be instantiated.
- Configure the result store with a URL for a known external store (e.g. Redis); expect: the right store type is chosen and the URL is passed through unchanged.
- Configure a store by a name that does not exist; expect: an import error, not a silent fallback.
- Configure a store whose name cannot be resolved (resolver raises ValueError); expect: a clear "improperly configured" error.
- Configure a store as a module instead of a class; expect: an "improperly configured" error.

**Concurrency / thread safety**
- Run a worker with a threaded pool, enqueue one job (6 + 9), fetch the result with a 10s timeout; expect: result 15.
- During that run, track every call on each result-store instance; expect: no single store instance is ever used from more than one thread (each worker thread gets its own).

**Worker shutdown**
- Stop the embedded worker after the job; expect: the worker thread exits within 10s, otherwise the test fails loudly (a stuck shutdown is an error, not ignored).
