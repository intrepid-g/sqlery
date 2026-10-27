### `t/unit/worker/test_revoke.py`

Tiny file: one test covering the worker's in-memory set of revoked (cancelled) job ids.

**Revocation registry**
- Mark job "foo" as revoked; expect: "foo" is reported as revoked.
- Then remove "foo" from the revoked set; expect: "foo" is no longer reported as revoked (a revocation can be cleared, e.g. on expiry or after the job is skipped).

Portable guarantee for sqlery: a cancelled job id is remembered by whoever claims jobs, the claimer skips it, and clearing the cancellation restores normal behaviour.
