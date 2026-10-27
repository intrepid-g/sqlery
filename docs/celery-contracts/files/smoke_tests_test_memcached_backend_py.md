### `t/smoke/tests/test_memcached_backend.py`

Portable guarantee: results are stored in a real result store and retrievable, large or failing results are surfaced rather than lost, and dependent-job completion tracking survives result expiry.

**Result roundtrip**
- Enqueue one job (2 + 2), run the worker; expect: result 4 retrievable within the timeout, job state is success.

**Dependencies (chord = fan-in)**
- Enqueue 5 jobs (i + i for i in 0..4) plus one dependent job that sums their results; run the worker; expect: the dependent job runs after all 5 finish and returns 20.

**Oversized results**
- Enqueue a job returning a result larger than the store's item limit (2 MB vs 1 MB); expect: the result is NOT silently dropped, fetching it raises an error, and the job state is failed.

**Result expiry vs. pending dependencies**
- Set result expiry to 2s but the dependency-tracking expiry to 60s. Enqueue 3 jobs that each take 4s (longer than result expiry) plus a dependent job collecting their results; expect: the dependent job still runs and receives all 3 results (here `[None, None, None]`); early-finished results expiring does not break completion tracking.
