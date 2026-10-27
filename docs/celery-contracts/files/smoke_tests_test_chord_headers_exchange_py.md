### `t/smoke/tests/test_chord_headers_exchange.py`

**Routing through a custom exchange (headers-based routing)**
- Configure two queues: a default queue and a special queue that only receives jobs carrying specific routing headers; disallow auto-creating unknown queues. Run the worker consuming only the special queue; expect: jobs tagged with those headers and that queue are delivered to and run by that worker.

**Dependent jobs (chord = group of jobs plus a final job that waits for all of them)**
- Enqueue 2 header jobs (1+1, and 2+2 delayed by 5 seconds) and 1 final job that depends on both, all routed via the special queue with the same headers; expect: the final job runs exactly once, after both header jobs finish, and its side effect (writes "chord_body_ran" to an external store under a unique key) appears exactly once (a list containing a single entry).
- The final job must run even though its routing is non-default (custom headers and queue); expect: routing metadata is preserved on the dependent job and on the completion trigger, not lost.

**Delayed jobs and retries**
- One header job is delayed (countdown 5s); expect: the worker logs a "Retry in" message (the dependency check re-schedules itself while the delayed job is pending), and the final job still runs once the delayed job completes.

**Timeouts and eventual completion**
- Poll the external store once per second up to the result timeout; expect: the final job's output shows up within the timeout (no hang, no lost job).

**Isolation**
- Results use a file-based result store in a temp directory and each run uses a unique key; expect: no cross-test interference.
