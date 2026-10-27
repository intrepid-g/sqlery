### `t/smoke/tests/quorum_queues/test_default_queue_type.py`

Mostly broker-specific (RabbitMQ exchange/queue types); one portable guarantee, and the test is a known failure (xfail, non-strict).

**Default queue configuration is honoured**
- Configure the system defaults (default exchange type "topic", default routing key "celery"), enqueue one no-op job onto the worker's queue, run the worker; expect: job done, result retrievable within the timeout (returns nothing).
- After that job, inspect the implicitly created default queue/exchange; expect: it reflects the configured default type, not the built-in fallback ("direct"). Known gap: currently the fallback is used and the configured default is ignored (marked expected-failure, non-strict, so a fix will not break the suite).

**Portable takeaway for sqlery**
- Configured queue defaults must apply to implicitly created queues (jobs enqueued without an explicit queue setting); a job routed to the default queue must still run to completion regardless of those defaults.
