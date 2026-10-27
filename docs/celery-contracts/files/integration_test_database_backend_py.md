### `t/integration/test_database_backend.py`

Portable guarantee: result storage in a SQL database survives transient database trouble, bounded by a configured retry budget, and surfaces a clear error when the budget runs out.

**Writes (storing a job result)**
- Database commit fails once (simulated disconnect) while storing a job's SUCCESS result; retries are enabled; expect: the store is retried automatically, the commit is attempted exactly 2 times, the result is persisted with status SUCCESS and the exact payload.
- Database commit fails on every attempt; max retries is 2; expect: exactly 3 attempts (1 initial + 2 retries), then a distinct "backend store" error is raised to the caller, not silently swallowed.

**Reads (fetching a job's status/result)**
- Job result already stored; the first read query fails transiently (simulated read error), bypassing any cache; expect: the read is retried, 2 query attempts total, and the returned status and result are correct and complete.

**Retry configuration honoured**
- Retry on/off, max retry count, and base/max sleep between retries are configurable; expect: backoff delays stay within the configured bounds (tests use 1-5 ms) so retries are fast but real.

**Setup**
- Tables are created at startup on request, and an initial write works before any failure injection; expect: the schema persists across separate database sessions (file-backed, not a per-connection in-memory database).
