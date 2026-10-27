### `t/unit/app/test_builtins.py`

Celery's built-in housekeeping and canvas tasks (cleanup, map, chunks, group, chain, chord). Note: the starmap, map and accumulate-without-index checks are weakly asserted upstream; the intended guarantees are listed.

**Result-store cleanup**
- Run the built-in cleanup job; expect: the result store's cleanup routine is invoked (expired results/old rows purged).

**Result accumulation (collecting parent results)**
- Accumulate values 1,2,3,4 with an index of 0; expect: only the first value (1) is returned.
- Accumulate values 1,2,3,4 with no index; expect: all values come back unchanged, in order.

**Map over inputs**
- Run one job function over inputs (2,2), (4,4), (8,8) with a multiply job; expect: results 4, 16, 64, one per input, in order.
- Same with argument-unpacking (starmap) form; expect: 4, 16, 64.

**Chunking (batching many inputs)**
- Split 3 inputs into chunks of size 1; expect: the chunk-splitting routine runs and fans the work out as separate jobs.

**Group (fan-out of independent jobs)**
- Enqueue a group while in eager/inline mode; expect: jobs run inline instead of being sent to the queue.
- Enqueue a group of 2 jobs; expect: each job is dispatched with the shared group id, over one shared producer connection, and the group is registered as a child of the currently running job (so it can be traced).
- Enqueue a group with "add to parent" disabled; expect: the group is NOT registered under the running job.

**Chain (sequential jobs)**
- Call the bare chain job directly instead of through a chain; expect: a not-implemented error (chains must be built, not called).

**Chord (fan-out then one callback when all finish)**
- Enqueue 10 add jobs plus a sum callback; expect: a result handle is returned and it links to the header group as its parent.
- Give a chord header that is a plain list rather than a group; expect: it is accepted and treated as a group.
- Enqueue a chord passing a group id option; expect: the callback job carries that group id.
- Enqueue a chord passing a chord id option; expect: the callback job carries that chord id.
- Run a chord in eager mode with 10 jobs (i+i) and a sum callback; expect: callback result 90.
- Run a chord in eager mode where extra args [1] are passed to each header job (i+1); expect: callback result 55 (args reach every header job).
