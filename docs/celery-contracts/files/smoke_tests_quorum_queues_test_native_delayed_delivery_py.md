### `t/smoke/tests/quorum_queues/test_native_delayed_delivery.py`

Portable guarantee: a job scheduled for later runs once, at or after its due time, never early; due-in-the-past or zero/negative delay runs immediately; cancelling before due prevents the run. (The broker delay-queue topology checks are Celery/RabbitMQ-specific.)

**Run-at-a-time scheduling**
- Enqueue 1 job with a 5s delay; expect: job not run early, result available within 10s, status success.
- Enqueue 1 job with a due datetime 5s ahead; expect: runs after ~5s, success.
- Enqueue 1 job with the due time given as an ISO-8601 string; expect: parsed, runs after ~5s, success.
- Enqueue 1 job with a due time 5s in the past; expect: runs immediately, success.
- Enqueue 1 job with delay 0; expect: runs immediately, success.
- Enqueue 1 job with negative delay (-5s); expect: treated as due now, runs immediately, success.
- Enqueue 1 job with a very short delay (1s); expect: runs after ~1s, success.
- Enqueue 1 job with no explicit queue, resolved by routing rules, no delay; expect: runs via the routed/default queue, success.
- Enqueue 1 job with no explicit queue (routing rules) and a 5s delay; expect: delayed, then runs via the routed queue, success.

**Long and pending delays**
- Enqueue 1 job due 25 hours ahead (longer than any single hop); expect: accepted, status stays pending, not ready, nothing runs.

**Many jobs and ordering**
- Enqueue 5 jobs with the identical due time; expect: all 5 run, all success.
- Enqueue 100 jobs with the identical due time (2s ahead); expect: all 100 run within 10s, all success, none lost.
- Enqueue 3 jobs due at +2s, +4s, +6s; expect: they complete in due-time order, each strictly after the previous.

**Cancellation**
- Enqueue 1 job with a 10s delay, cancel it while pending; expect: status pending before cancel, revoked after cancel, job never runs (checked after the due time passes).

**Dependencies with delays**
- Chain of 3 jobs add(1,2) -> add(+3) -> add(+4), each with a 2s delay; expect: each waits its delay, results flow through, final result 10 within 15s.

**Configuration (Celery-specific, translated)**
- Delay infrastructure is created with the expected shape regardless of queue type (quorum or classic): a fixed set of delay levels, each level's delay doubling, each level hands off to the next-shorter one, final level hands off to the real delivery path; all exchanges are topic type. Sqlery equivalent: scheduler config must be consistent for any supported database backend.
