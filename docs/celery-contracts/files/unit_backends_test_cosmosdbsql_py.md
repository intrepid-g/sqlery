### `t/unit/backends/test_cosmosdbsql.py`

Vendor result-store backend (CosmosDB). Mostly client wiring; the portable guarantee is: result stored and retrievable, final state wins, forgetting is safe, bad config fails loudly.

**Configuration errors**
- Start a result store without its required third-party driver installed; expect: a clear configuration error, not a crash later.
- Give a malformed connection URL (no host, no key, or garbage); expect: configuration error at startup.
- Give an unknown consistency/isolation level; expect: configuration error.
- Give an empty, whitespace-only or missing partition/shard key; expect: rejected with a value error.
- Give a URL with no port or port 443; expect: secure connection on 443. Give another port (8080); expect: plain connection on that port.

**Setup**
- Open the store; expect: database and collection/table created on first use only, exactly once per store instance, not at construction.

**Store and read results**
- Store a result for a job id, then read it; expect: the value comes back from the correct key.
- Read a job id that was never stored; expect: none returned, no error.
- Read several job ids in one call; expect: each looked up by its own key.
- Store a result; expect: written as a single upsert keyed by job id.

**State transitions**
- Mark a job started, then done (42); expect: final state SUCCESS with result 42 (later state overwrites earlier one under the same key).
- Mark a job done (42), then a redelivered duplicate marks it started; expect: still SUCCESS with 42, and only one write happened (success is never downgraded).

**Forgetting results**
- Delete a result; expect: removed by its key.
- Delete a result that expired, was never stored or was already forgotten; expect: no error (no-op).
- Delete the same result twice; expect: second call is a silent no-op.
- Delete fails for any other reason (server error 500); expect: the error is raised to the caller, not swallowed.
