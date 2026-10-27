### `t/smoke/tests/test_task_queue_default_exchange.py`

**Queue routing and delivery (broker-backed, portable guarantee)**
- Define a queue with an explicitly empty-named routing target while the system has no default routing target configured; expect: the queue keeps its own routing target and is not silently rewritten to a default.
- Declare that queue, publish one message addressed to it by queue name, then fetch from it; expect: exactly that message comes back with its payload intact ("message"), nothing lost.
- Publish with publisher confirmation enabled; expect: delivery still works and the message is retrievable (confirm mode must not break direct-to-queue delivery).
- Queue names are unique (random id) so scenarios never collide with leftovers from other runs; expect: isolation between runs.

**Portable sqlery takeaway**
- Enqueue 1 job to a named queue with no default queue configured; run the worker on that queue; expect: 1 job done, that queue empty, job not rerouted to another queue.
