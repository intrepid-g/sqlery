### `t/unit/utils/test_timer2.py`

Portable guarantees for the in-process timer that runs delayed callbacks (closest sqlery idea: the scheduler loop).

**Construction**
- Build a scheduler loop around an existing schedule (empty or already holding a pending entry); expect: it uses that exact schedule, untouched.
- Build a scheduler loop with an error handler and a max sleep interval but no schedule; expect: a fresh schedule is created carrying that handler and interval.

**Running delayed work**
- Schedule a callback 0.3s ahead and wait; expect: it runs within about 2s, and the loop is stopped cleanly afterwards.
- Ask the loop to exit after a delay with a priority; expect: it schedules a process-exit call at that delay and priority.
- Ensure-started on a loop already running; expect: nothing is started again (no duplicate loops).
- Ensure-started on a loop not running; expect: the start hook fires, then the loop starts.
- Enter a callback at a time with a priority; expect: it is delegated to the schedule as an absolute-time entry with the same priority.
- Enter a callback after a delay; expect: delegated as a relative-delay entry.
- Cancel a scheduled entry via its reference; expect: that entry's cancel is called.

**Loop behaviour**
- Loop with 3 ticks each waiting 3.33s, then a shutdown signal; expect: it sleeps 3.33s each time, the tick hook is called 3 times with 3.33, then it exits without killing the process.

**Unexpected problems and shutdown**
- Scheduler thread hits an unexpected error (OS error) while finding the next entry; expect: the process is terminated with exit code 1 rather than hanging silently with a dead scheduler.
- Stop the loop while the interpreter is tearing down (stop event raises TypeError because built-ins are already collected); expect: the error is swallowed and the stop event is still attempted, no crash.
