### `t/smoke/tests/test_task_queue_routing_key.py`

**Queue default routing key (broker-specific wiring, portable guarantee: a queue gets messages addressed to its default key)**
- Configure a queue collection with default routing key "tasks.default"; add a new queue with no explicit routing key via the "add" method; publish a message to that key; expect: the queue receives exactly that message with its payload intact.
- Same scenario, but register the queue by assigning it by name (dict-style); expect: identical behaviour, both registration paths give the queue the inherited default routing key.
- Publish is confirmed by the broker before the consumer reads; expect: the message is retrievable immediately afterwards (no lost enqueue).
- Read the message without acknowledgement; expect: the payload is returned unchanged.

**Portable note for sqlery**
- A queue created without an explicit routing/name override must inherit the system default; a job enqueued to the default target is claimed by a worker serving that queue, whichever API was used to register the queue.
