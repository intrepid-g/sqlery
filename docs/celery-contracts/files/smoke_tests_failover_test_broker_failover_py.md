### `t/smoke/tests/failover/test_broker_failover.py`

Portable guarantee: the queue backend can have more than one database/broker endpoint configured; if the primary becomes unavailable, workers and enqueuers continue on the standby without losing the ability to run jobs, and return to the primary when it is back.

**Failover when the primary dies**
- Configure two backends (primary and standby). Kill the primary, enqueue 1 job on the worker's queue; expect: job runs on the standby, result returned within the timeout and equal to the input.

**Reconnect to the primary**
- Kill the primary, enqueue job 1; expect: done via the standby. Then kill the standby and restart the primary, enqueue job 2; expect: job 2 done via the primary (worker reconnects to the original endpoint).

**Operator visibility**
- Kill the primary; expect: worker logs that it will retry using the next failover endpoint, and logs a successful connection to the standby (credentials masked in the logged URL).

**Precondition**
- The test cluster must have more than one endpoint configured; otherwise failover is not meaningful.
