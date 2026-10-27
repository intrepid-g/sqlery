### `t/integration/test_backend.py`

Mostly vendor wiring (Azure Blob result store, only runs when a real storage URL is configured). Portable guarantees only:

**Result storage round trip**
- Store 5 results (distinct keys, each marked success); fetch all 5 in one bulk read; expect: values returned in the same order as the keys requested, none lost or altered.
- After the bulk read, delete each of the 5 results; expect: deletes succeed with no error.

**Missing results**
- Ask for the result of a job that never existed; expect: "nothing" (None), not an error or a made-up value.

**Environment gating**
- No result-store configured or optional driver not installed; expect: tests are skipped, not failed (sqlery equivalent: Postgres-only checks skip cleanly on SQLite).
