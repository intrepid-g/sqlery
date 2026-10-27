### `t/smoke/tests/quorum_queues/test_chord_unlock_routing.py`

**Dependency unlock under custom routing**
- Configure the unlock/finalizer step of a dependent group to be routed to its own dedicated queue (topic-style routing), and run a worker that consumes both the default queue and that dedicated queue. Enqueue 10 groups, each with 3 parent jobs (add i+j) and 1 dependent job (summarize) that runs only after all 3 parents finish; expect: every one of the 10 dependent jobs runs and returns a result within the timeout, none stuck waiting.
- Expect: no group is left permanently pending because its unlock step was queued to a queue that was declared and bound but never consumed; the number of groups that failed or got stuck is reported (e.g. "3 of 10 failed").

**Load behaviour (known flaky)**
- Under load, routing the unlock step to a dedicated durable (quorum-style) queue is known to intermittently fail; the test is marked expected-to-fail (non-strict), so a pass is also acceptable. Portable guarantee for sqlery: a dependent job whose parents are all done must always be picked up, regardless of which queue it is routed to, even when many groups complete at once.

**Failure collection**
- Wait on every group's final result individually; one group's failure or timeout must not hide the others (collect all failures, then assert none).
