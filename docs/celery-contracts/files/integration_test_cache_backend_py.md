### `t/integration/test_cache_backend.py`

Note: the file is a single memcached-specific test. The portable guarantee is result retention and group (dependency set) bookkeeping surviving expiry.

**Results and expiry**
- Configure a short result expiry (1s) and a longer group-bookkeeping expiry (60s). Create a group of 2 jobs, finish one successfully (result 1), notify the group that the job finished. Wait 2s (past the result expiry); expect: the finished job's result is still readable straight from the store (not from an in-memory cache) and equals 1.
- Same setup; expect: the group record (its id and member job list) is still restorable and equal to the original after the job result expiry has passed.

**Cleanup**
- After the scenario, forgetting the job result, deleting the group, and deleting the group's completion-counter key all succeed, even if run in a finally block after a failed assertion (no leftover state).

**Skip rule**
- Scenario only applies when the result store is memcached; with any other store, skip rather than fail.
