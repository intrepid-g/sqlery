### `t/unit/contrib/test_manager.py`

This file tests Celery's integration-test helper (a "wait until ready" poller), not the broker. The portable guarantee is polling with bounded retries and clear surfacing of the final error.

**Waiting for a result (retry-until-ready)**
- Poll something that fails once ("not ready") then succeeds; expect: returns the successful value, 2 attempts made, the error callback called once with (the error, retry count 0, delay 0), nothing written to stdout.
- Same scenario with warnings on; expect: a line "Still waiting for <description>. Trying again now: <error>" on stderr.
- Same scenario with warnings off; expect: stderr stays empty.

**Retry limit exhausted**
- Poll something that always fails, with max 1 retry; expect: 2 attempts total, then the original exception (same object, not a wrapped copy) is raised to the caller.
- With warnings on and no description given; expect: one warning line using the default description "thing".

**Asserting something stays absent for a while**
- Poll something that keeps failing with the expected error, warnings on, max 1 retry; expect: returns None (the "not there" assertion holds), 2 attempts, one warning line on stderr.
