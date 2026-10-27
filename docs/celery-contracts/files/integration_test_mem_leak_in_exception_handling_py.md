### `t/integration/test_mem_leak_in_exception_handling.py`

**Failed jobs must not leak memory**
- Run 50 successful jobs as warm-up, then 500 jobs that each raise an unhandled exception; expect: every failure surfaced to the caller (500 raised), and process memory grows by less than a threshold (default 10% of baseline, configurable) after garbage collection.
- After the failures, run 50 more successful jobs; expect: the worker still processes normal jobs and memory stays flat (leak attributable only to failures would be a bug).

**Retries that end in failure must not leak memory**
- Run 100 jobs that retry 3 times (near-zero delay) and then fail with an unhandled exception; expect: the final failure is surfaced, and memory growth stays under a small fixed bound (about 3 MB).

**Deep error stacks must not leak memory**
- Run 200 jobs that fail from five nested calls deep, each holding local data in its frames; expect: stored tracebacks and frame locals are released after the job ends, and memory growth stays under about 4 MB.

**Cross-cutting guarantees**
- Failure records (error info, traceback) must not keep references to frames, locals or the exception after the job is finalized.
- Failing, retrying-then-failing and deeply nested failures all clean up equally; a long-running worker's memory stays bounded however many jobs fail.
