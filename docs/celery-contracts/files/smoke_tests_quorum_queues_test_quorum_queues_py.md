### `t/smoke/tests/quorum_queues/test_quorum_queues.py`

**Queue durability type (broker-specific; portable idea: queue storage guarantees)**
- Start the system with the worker's queue configured as a replicated/durable queue; inspect the queue via the management API; expect: the queue exists and is reported as the durable (quorum) type. Sqlery equivalent: jobs live in durable DB rows, never in memory only.

**Single job round trip**
- Enqueue one job onto the worker's specific queue with an immutable argument ("test_signature"); run the worker; expect: the job's result is retrievable within the timeout and equals the argument.

**Group of jobs (parallel fan-out)**
- Enqueue a group made of three differently-constructed groups (nested group, list-built group, generator-built group), each holding add(1,1) and add(2,2), onto the worker's queue; expect: all 6 jobs done, results returned in submission order [2, 4, 2, 4, 2, 4], within the timeout.
- Construction style (nested, list, generator) must not change the outcome.
