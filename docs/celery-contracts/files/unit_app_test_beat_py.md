### `t/unit/app/test_beat.py`

Celery beat = the periodic scheduler. Translated to sqlery: scheduled tasks, a scheduler loop ("tick") that enqueues jobs when due, a persisted schedule store, and an optional liveness/control endpoint.

#### Scheduled task entry
- Create a scheduled task with a 10s interval; expect: it has a last-run time, run count 0.
- Advance it once; expect: run count 1, last-run time moves to at least the given time.
- Brand-new 10s task; expect: not due, with about 10s remaining. Same task with last run 10s in the past; expect: due.
- Entry keeps its app/schedule binding; expect: its schedule uses the same app.
- Serialise (pickle) a task and restore; expect: same schedule.
- Compare/order two tasks (and a task vs a foreign object); expect: no crash, order is not significant.
- Update a task from another definition; expect: schedule, args, kwargs and options are all replaced.
- Task representation is printable.
- A lazy function wrapper (deferred call) gives the same result via direct call or `delay()`.

#### Dispatching a due task
- Dispatch a scheduled task; expect: the registered task instance is the one enqueued.
- Task with args/kwargs of None, or set to None afterwards; expect: still enqueued (treated as empty), no crash.
- Task with args (101,) and kwargs None; expect: enqueued with args [101].
- Dispatch; expect: the enqueued job is tagged as coming from the scheduler (header), and any caller-supplied headers are preserved alongside.
- Dispatch returns no result, a non-result object, or a real result; expect: all handled, log says "sent", with the job id only when one exists.
- Scheduler can accept a custom schedule dict as its backing data; set_schedule replaces it.
- Tasks given as a name+dict are turned into entries; an existing entry passes through unchanged.
- Send via the scheduler forwards name and options (e.g. countdown) to the app's enqueue.

#### Periodic persistence cadence
- With a "sync every N dispatches" setting, expect: schedule is saved after every N dispatches, counter increments per dispatch and resets after a save; with the setting off, expect: no save triggered by dispatch.
- Scheduler info string is available.

#### Default entries
- Result expiry unset; expect: no backend-cleanup task added.
- Expiry set and backend does not expire results itself; expect: cleanup task added.
- Backend auto-expires; expect: cleanup task not added.

#### Tick: timing and ordering
- One always-due task; expect: dispatched, tick returns 0 delay.
- One not-due task with 1s remaining; expect: tick returns a small positive delay, slightly under 1s (10ms safety margin).
- Task due within 10ms; expect: delay 0 (do not oversleep).
- Task next due far beyond the maximum interval; expect: tick sleeps at most the max interval.
- Several tasks (36s to 650s away); expect: delay is for the soonest.
- Task reports no remaining time; expect: sleep max interval minus 10ms.
- Sub-second schedule (0.5s) with fractional clock; expect: fractional part is not dropped when computing next time.
- Time computation is timezone-independent: same instant in UTC and another zone gives same result.
- Heap rebuilt from schedule contains the entry at its computed time.
- Replace a task with the same name but a new interval; expect: next tick uses the new interval.
- Heap is built lazily (not at construction).
- Top-of-queue task not due while a due task sits behind it; expect: the not-due one is pushed back, the due one runs on the next tick, nothing is sent on the first.
- A task's due-check mutates the queue (another task inserted in front) while running; expect: do not corrupt the ordering, return a negative/immediate delay so the loop re-evaluates, nothing dispatched stale; the inserter stays first and the checked task stays queued.
- Same race when the due-check also advances the clock past the max interval; expect: delay computed from fresh time, not the stale time, whether due or not.
- Merge a freshly loaded schedule into a live one; expect: removed tasks disappear, new ones appear, existing ones take new definitions.

#### Tick: failure handling
- Enqueue fails with a scheduling error; expect: error logged, tick still returns normally (0), loop continues.
- One task's due-calculation raises (bad schedule) while building the queue, plus one healthy due task; expect: healthy task still dispatched, error logged naming the broken one, broken one retried after the max interval.
- A task's due-check raises when it reaches the front; expect: nothing sent, error logged, task rescheduled at max interval, tick returns that delay (loop does not die).
- Runtime error "dict modified during iteration" in due-check class is a known hazard; expect: scheduler survives.

#### Missed runs and catch-up
- After restart, persisted tasks with last-run 2 minutes ago: those whose cron slot was missed (every 10 min and every minute) run once each; the one not missed does not run; the next tick is scheduled at the start of the next minute.
- Cron with a "starting deadline" setting: missed run 20 min ago within 30 min deadline; expect: dispatched.
- Missed run 35 min ago, past the deadline; expect: skipped.
- Missed run exactly at the deadline; expect: still dispatched (inclusive boundary).
- Applies to non-uniform cron (:00, :45), using the most recent feasible slot.

#### Time zones and daylight saving
- Solar (sunrise) task across spring-forward and fall-back days: before sunrise, nothing sent and delays positive; at sunrise exactly one dispatch, run count 1, last-run equals the dispatch instant; a second later nothing more is sent.
- Daily "relative" task across spring-forward (23-hour day): runs at midnight, next run is 23 real hours later, does not run at 23:59:59, runs at next midnight, and not twice.
- Timezone-aware/naive conversion helpers always yield an aware datetime when UTC is on or off; "to local" gives naive in UTC mode, aware otherwise.

#### Schedule change detection
- Two schedules equal when same interval, same cron, same args, kwargs, options, task, same keys.
- Not equal when any of those differ, when keys differ, or when interval vs cron types differ.
- None vs schedule: not equal; None vs None: equal.

#### Persistent schedule store
- Stored entries are loaded into the live schedule at startup; changes written back (schedule == store).
- Store cannot be opened (OS error); expect: it is deleted and recreated; removing files ignores "not found" but raises on other errors (e.g. permission).
- Store version, timezone or UTC setting changed; expect: store cleared and rebuilt.
- Store entries missing or wrong type; expect: recreated silently with no warning when merely new/empty.
- Store corrupted (decode error, db-module error, unpickle error) and cannot even be reset; expect: corrupted store destroyed and recreated.
- Stored entry no longer valid under current rules (e.g. cron for 30 Feb from an older version); expect: warn once, reset entries to empty.
- Sync with no store open; expect: no crash.
- Close twice; expect: second close is a no-op that does not reopen the store or make connections.
- Close before the store was ever set up; expect: no store opened, no connection made.

#### Scheduler shutdown / resource release
- Close; expect: broker connection and channel released, cached handles dropped, store synced and closed (persistent), second close releases only once.
- Subclass using plain attributes for connection/producer; expect: also released.
- Close without ever using the broker; expect: creates no connection or producer.
- After close, using the scheduler again; expect: fresh connection, producer and channel (old ones stay closed) and publishing works.
- Producer creation fails; expect: error surfaces but the connection is still released on close.
- Final sync fails; expect: the sync error is raised, but connection, channel and store are still closed.
- Broker connection/channel errors during release; expect: ignored, close completes.
- Unexpected release error; expect: raised after cleanup; if sync also failed, the sync error wins and the release error is logged.
- Store close fails; expect: connection still released; sync error takes priority and is logged, otherwise the store error is raised.

#### Service lifecycle
- Service is serialisable; hostname and remote-control flag survive (so a spawned child process does not silently re-enable control).
- Start; expect: scheduler loaded with stored entries; effective max interval recorded; last-tick timestamp stamped.
- Sync on the service saves and closes the store; repeated sync safe; stop with or without waiting sets the shutdown flag.
- Start as thread or embedded process with shutdown already set; expect: exits cleanly.
- Tick raises SystemExit; expect: service shuts down cleanly.
- Shutdown requested during first tick; expect: loop ends after that one tick.
- Embedded service (thread): run starts the service, stop stops it; embedded mode forces remote control off unless explicitly overridden.
- Explicit remote-control argument beats the config setting.

#### Liveness / remote control (optional "is the scheduler alive" ping)
- Off by default; only started (and stopped on exit) when enabled.
- Node name defaults to `celerybeat@host`; bare value becomes the host; full name kept; host abbreviations expanded.
- Ping replies `{ok: pong}`.
- Staleness threshold: defaults to twice the effective loop interval (module default if unset, scheduler's own interval if it chose one), never below a floor, or explicitly configured; 0 disables the check.
- No tick recorded yet: not stale and no age.
- Last tick older than threshold: stale; fresh tick: not stale.
- Stale scheduler gets a ping addressed to it or broadcast with no destination or matching pattern; expect: no reply (silence signals failure) and one warning.
- Messages addressed to other nodes: ignored without warning or error.
- Healthy scheduler: replies to broadcast ping; unknown commands (shutdown, revoke, stats...) get no reply; non-dict payload ignored without error; handler exception swallowed.
- Start/stop: runs in a daemon thread, start is idempotent (no second consumer), stop before start or twice is a no-op, a thread that will not die within timeout is warned about and kept.
- Transport without fanout support: refuse to start, warn once naming the transport, one connection probe only.
- Connection drop during run: reconnect and keep listening.
- First connect honours the "retry on startup" setting (fall back to general retry setting when unset); only the first connect is a startup connect; restart resets that.
- Max retries honoured; retry disabled means zero retries.
- Failed connection is released.
- Retries exhausted or retry disabled (at first connect or on a dropped link): give up once, log a warning/error naming the relevant settings, no endless retry loop.
- Stop during backoff interrupts the wait promptly, with no spurious warning.
- Error occurring during deliberate shutdown: quiet exit, no warning, no retry.
- Retry attempt logs an error.

---
_Generated by [Claude Code](https://claude.ai/code)_
