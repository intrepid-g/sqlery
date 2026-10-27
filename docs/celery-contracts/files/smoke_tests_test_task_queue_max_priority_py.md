### `t/smoke/tests/test_task_queue_max_priority.py`

**Priority ordering**
- Configure a queue set with a default max priority of 10, then add a new queue (no explicit priority setting); expect the queue inherits the default max priority.
- Enqueue a low-priority job (1) then a high-priority job (9) on the same queue before any worker consumes; expect the high-priority job is claimed first, then the low-priority one.
- Same scenario, with the queue registered via an "add" call and via direct assignment by name; expect identical behaviour.

**Delivery guarantees**
- Enqueue with publish confirmation enabled (the enqueue is acknowledged as durably stored before returning); expect both jobs are present and retrievable afterwards.
- Declare the same queue twice (declaration is idempotent); expect no error and no duplicate queue.
- Claim jobs with auto-acknowledge; expect each job claimed exactly once, none redelivered.

**Portability note**
- Backend wiring is RabbitMQ-specific (no result backend); the portable guarantee is only: claim order is by priority, not insertion order, when jobs are waiting together.
