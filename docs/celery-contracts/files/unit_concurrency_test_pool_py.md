### `t/unit/concurrency/test_pool.py`

**Pool setup**
- Create a worker pool with a concurrency limit of 2; expect: the limit is 2 and no worker processes exist yet (nothing is started until the pool is started).

**Job results and callbacks (disabled in Celery, but states the intended guarantee)**
- Start a pool of 2; submit job A (returns 10 squared), job B (raises), job C (returns 20 squared); expect: A returns 100, C returns 400, and B returns its captured exception info instead of crashing the pool.
- Each successful job fires its success callback exactly once with its return value (100, then 400).
- A failing job fires its error callback exactly once, with the original exception type and message preserved ("FOO EXCEPTION").
- After a failure, the pool keeps working: a further job submitted afterwards (30 squared) still runs and returns 900, and its callback fires.
- Callbacks fire in completion order, one per job, none lost or duplicated (4 jobs, 4 callbacks).
- Stopping the pool after all work finished completes cleanly.
