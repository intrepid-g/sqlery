### `t/unit/concurrency/test_eventlet.py`

Green-thread (eventlet) worker pool: pool sizing, killing a running job, timers, startup patching.

**Startup / environment**
- Start a worker in green-thread mode; expect: the runtime is monkey-patched before any job code runs.
- Same start with a "blocking detection" env var set to 10.3; expect: the detector is armed with that threshold (both timeout values), so a job that hogs the loop gets reported.

**Pool lifecycle**
- Start the pool, submit one job, stop the pool; expect: job tracked under a single id, a worker id is reported.
- Stop a pool that was never started or already torn down; expect: no error.

**Resizing capacity**
- Pool of 10, grow by 2; expect: limit 12, pool size 12, two extra slots released.
- Pool of 10 with 8 free slots, shrink by 2; expect: limit 8, size 8, free slots 6.
- Pool of 1 busy with job A, job B blocked waiting for a slot, then grow by 1; expect: B starts promptly without A finishing.
- Autoscaler on a pool of capacity 3 (max 10, min 3) with 5 reserved jobs; expect: scales up by exactly 2, based on configured capacity, not on how many jobs are currently running.
- Report concurrency while only 1 of 5 slots is busy; expect: reports 5 (capacity), not 1.

**Introspection**
- Ask pool for stats; expect: implementation name, max concurrency, free slots, running jobs.

**Cancelling a running job**
- Terminate a running job by id; expect: it is killed once and a completion hook is registered once.
- Job that is killed mid-run (kill signal inside it); expect: reported as a failed/no-result outcome (false, no value, no error), not a crash of the worker.
- Job that returns normally; expect: its result is returned unchanged.
- Job finishes (or is killed); expect: its entry is removed from the tracking map, no leak.

**Timers / scheduled callbacks**
- Schedule a callback after a delay; expect: it is queued.
- Callback finishes or is cancelled via kill; expect: removed from queue; if it was cancelled mid-wait the entry is cancelled too.
- Clear all timers, including one whose cancel raises; expect: queue emptied, error swallowed.
- Cancel a timer reference; expect: cancelled. Cancel raising the kill exception; expect: swallowed.
