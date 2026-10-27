### `t/unit/tasks/test_chord.py`

Celery "chord" = a group of jobs plus a callback job that runs once all of them finish. Closest sqlery idea: a job with dependencies on N other jobs.

**Releasing the callback (dependency check)**
- Create 3 parent jobs, all done with results 2, 4, 8, 6; run the check; expect: callback enqueued exactly once, receiving the list of parent results in order, with no re-check scheduled.
- Same, but the callback is supplied in serialized (dict) form; expect: it is rebuilt and enqueued with the same results.
- Parents not all finished; expect: callback NOT enqueued, check rescheduled after the configured interval, with the configured max retries.
- Check interval defaults to the global configured retry interval, and an explicit per-call interval overrides it.
- Rescheduled check keeps its routing options (e.g. exchange/queue type) rather than dropping them.
- Gathering the parent results uses the configured join timeout (default and custom values both honoured), and propagates parent errors.

**Failure handling**
- Create parents where one fails (raises); expect: callback never runs, callback itself marked failed with a dependency error, no retry/re-check.
- That dependency error names the failed parent job when it can be identified; when the culprit cannot be identified, the callback is still marked failed with a dependency error.
- All parents done but enqueueing the callback fails (I/O error); expect: callback marked failed with a dependency error, not silently lost.
- Checking parent readiness itself blows up (unexpected error); expect: the error propagates to the caller, it is not swallowed.

**Submitting a group with a callback**
- Group of 10 jobs with a callback submitted in eager (run-inline) mode; expect: callback result equals the sum of all parent results.
- Submit group with callback normally; expect: a result handle with an id is returned, the callback's original definition is not mutated (no task id stamped on it), and the run step is invoked.
- Chord-level options (e.g. check interval) and a caller-supplied task id are passed through to the run step unchanged.
- Nested groups: outer group of two inner groups-with-callbacks, where one inner group has only a single job; expect: submission succeeds and returns an id (regression: used to crash with KeyError).
- Group-with-callback definition survives serialization round-trip (pickle) with identical kwargs; kwargs must not nest/recurse on repeated serialize/deserialize.
- The unlock/check task is registered by default.

**Adding jobs to a running group**
- A job that is not part of any group tries to add a job to it; expect: error (ValueError).
- A job inside a group adds a job (lazy mode); expect: new job is tagged with the group id, callback, and a fresh task id, is registered with the result store, but is NOT enqueued yet; the signature is returned.
- Same, non-lazy; expect: new job tagged identically, registered, and enqueued immediately, returning its result handle.

**Running the group**
- Running a group (as a generator or a list) with a callback registers the callback with the result store once per run (2 runs, 2 registrations).
- The expected group size is recorded in the result store when the group starts, so completion can be detected.
