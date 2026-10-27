### `t/unit/backends/test_gcs.py`

Portable guarantee: a result store on cloud object storage keeps results retrievable by key, honours expiry, fails fast on bad configuration, survives process forks, and reports errors in fan-in (chord) completion instead of losing them.

**Startup and configuration (fail fast)**
- Required storage client library not installed; expect: refuse to start with a clear "you must install" error.
- Required counter-store library not installed; expect: same clear error.
- No bucket configured; expect: startup error naming the missing bucket.
- No project configured; expect: startup error naming the missing project.
- Negative result expiry; expect: startup error "invalid ttl".
- Expiry of 0 (keep forever) but the counter store has no expiry policy enabled; expect: startup error "missing TTL policy".
- Positive expiry but the bucket has no delete lifecycle rule; expect: startup error "missing lifecycle rule".
- Counter-store project not given; expect: defaults to the main project. Given explicitly; expect: the override is used.
- Configure from a URL (bucket, project, optional folder, with or without trailing slash); expect: bucket, project and base path parsed, slashes stripped.

**Storing and reading results**
- Set a key; expect: value uploaded with the retry policy; with expiry, an expiry time in the future is stamped; without expiry, none.
- Get an existing key (bytes key); expect: its content returned, key decoded to text.
- Get a key that does not exist; expect: None returned, no exception.
- Delete an existing key; expect: it is checked and removed.
- Delete a missing key; expect: silent no-op, nothing deleted.
- Get many keys; expect: one value per key.
- Key location with a base path; expect: "base/key". Without; expect: just "key".

**Process forks**
- Use the storage client repeatedly in one process; expect: same client reused. After the process id changes (fork); expect: a fresh client built for the project.
- Same for the counter-store client; expect: reused within a process, rebuilt after fork, new pid recorded.
- After a fork, whichever client is requested first; expect: both clients are rebuilt, neither stale.

**Expiry policy detection**
- Counter-store expiry policy state enabled; expect: reported enabled. State "needs repair"; expect: reported not enabled.

**Counters and fan-in completion (chord, ~ job dependencies)**
- Increment a counter for a key; expect: one atomic counter-store operation.
- Start a fan-in (group of jobs then a final job); expect: the counter gets a default 24h expiry, then normal start-up proceeds.
- Last member of the group reports in (count equals size); expect: group restored, results joined with the configured timeout and error propagation, final job enqueued with the ordered results, counter deleted.
- Group cannot be restored (raises); expect: final job marked errored, nothing joined.
- Group restores to nothing; expect: same error reported, nothing joined.
- Joining results raises (a member failed); expect: final job marked errored with the original error as cause, group results and counter still cleaned up.
- Enqueueing the final job raises; expect: error reported as "Callback error" with the exception type, cleanup still happens.
- Delete a counter; expect: its document removed.
- Expire a counter; expect: expiry stamped at least now plus the given seconds.
- Counter documents; expect: addressed by key text inside one named collection.
