### `t/unit/backends/test_consul.py`

Portable guarantee: this is a vendor key-value result store (Consul). Wiring is Celery-specific; the portable contract is "result stored and retrievable, deleted on request, expiry handled by the store, keys normalised".

**Result storage**
- Store a result for a job, then read it back by its key; expect: the same payload returned.
- Store a result; expect: the store reports success (true).
- Delete a job's result; expect: the store reports success (true), and the result is gone.

**Expiry and consistency**
- The result store declares that it expires old results by itself; expect: cleanup does not need to purge it manually.
- The result store reads with strong consistency; expect: a result just written is visible to the next read (no stale reads).

**Bad / unusual input**
- Look up a result using a job key given as text; expect: the key is used unchanged.
- Look up a result using the same job key given as bytes; expect: it is decoded and resolves to the same key as the text form (no duplicate or mismatched entries).
