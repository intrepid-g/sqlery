### `t/unit/tasks/test_trace.py`

**Basic execution and hooks**
- Run a job `add(2,2)`; expect: result 4, no error info.
- Job with a before-start hook; expect: hook called before the job runs.
- Job with an on-success hook; expect: hook called after success.
- Job with an after-return hook; expect: hook called after the job finishes, whatever the outcome.
- Run a job; expect: pre-run, post-run and success signals fire; the success signal carries a float runtime equal to the runtime reported to the caller.

**Hook failures must not corrupt job outcome**
- On-success hook raises after the result was stored (worker mode); expect: job stays SUCCESS, result 4 returned, no failure handling, no failure or internal-error signal, success signal still fires, after-return still called with SUCCESS, the hook error is logged.
- Same hook failure in eager (inline) mode; expect: the error propagates to the caller.
- On-success or after-return hook raises Reject (worker mode); expect: Reject is not swallowed.
- On-success hook raises MemoryError; expect: it propagates, not swallowed.
- After-return hook raises, job succeeded; expect: job not recorded as failed, hook error logged once.
- After-return hook raises, job also failed; expect: failure recorded exactly once (by the job's own exception), hook error logged once.
- After-return hook raises in eager mode; expect: error propagates to caller.

**Job outcomes and error handling**
- Job raises an ordinary exception (KeyError); expect: state FAILURE, the exception kept as the return value, traceback frames cleared to free memory.
- Same with propagate enabled; expect: exception re-raised to caller.
- Job raises Retry; expect: state RETRY, the Retry object kept, traceback cleared.
- Job raises Ignore; expect: state IGNORED, traceback cleared.
- Job raises Reject; expect: state REJECTED, traceback cleared.
- Job raises SystemExit; expect: it propagates out (worker is allowed to shut down), not recorded as a normal failure.
- Clearing a traceback works when given an exception, nothing, or a non-exception (string); expect: local variables of frames dropped in every case.

**Log policy**
- Reject logs as reject, Ignore as ignore; an exception listed in the job's expected "throws" logs as expected, any other exception as unexpected, an internal error as internal.
- Job name: use the "shadow" override name when set; empty, null or missing shadow falls back to the registered name.

**Result storage**
- Result backend cleanup raises an ordinary error after a run; expect: job unaffected. Cleanup raises MemoryError; expect: it propagates.
- Result backend fails on both storing success and storing failure; expect: error surfaces (worker mode), not silently lost.
- Backend error escapes the run wrapper; expect: reported as a failure with error info, not a crash.
- Error escaping the tracing machinery itself (outside the job body); expect: reported as an internal error, internal-error signal sent, tracer kept for reuse.
- Eager (inline) run of a job whose results are not ignored; expect: result is not stored by default; stored only if the "store eager results" setting is on.
- Eager failure; expect: error not stored by default, stored when the eager-store setting is on; failure callbacks still invoked.
- Worker-mode failure with no request context: error stored unless the job ignores results and does not store errors when ignored; job that ignores results but opts into storing errors still stores them.
- Ignore-result precedence: per-request flag beats job setting, which beats app default (both directions: request false overrides job true and app true; request true overrides job false).
- Reject handling logs the rejection.

**Dependencies, callbacks and chains (sqlery: dependent jobs)**
- Job with a follow-up callback finishes; expect: follow-up enqueued with the result as input, parent and root ids propagated, priority default.
- Chain of several follow-ups; expect: only the next is enqueued, with the remainder passed along.
- Inherit-parent-priority setting on; expect: follow-up gets the parent's priority (e.g. 42).
- Enqueueing the follow-up fails to serialise; expect: the parent job is marked FAILURE.
- Callbacks mixing single jobs and groups of jobs; expect: each enqueued with the result, parent and root ids.
- Callbacks that are only groups; expect: each group enqueued.
- Job that is part of a barrier group (chord); expect: result stored with the group reference, not treated as eager storage.
- Dispatching follow-ups must not mutate the request's follow-up list (length unchanged after dispatch).

**Duplicate delivery (redelivered job already succeeded)**
- Dedup on, redelivered job id already succeeded; expect: first run returns 2, second run does nothing (no result, no error).
- Dedup on but stored state is not SUCCESS (still pending); expect: job runs again each time.
- Result lookup raises "result not found"; expect: treated as not done, job runs.
- Id already in the worker's in-memory successful set; expect: skipped with no side effects, and follow-ups not re-dispatched.
- Dedup via stored result with follow-ups pending; expect: callbacks and remaining chain are dispatched exactly once with the stored result (parent id = job id, root id falls back to job id when absent; multi-step chain passes the remainder), then the id is added to the in-memory successful set.
- Chain plus callbacks together; expect: both dispatched (2 sends).
- Empty chain and callbacks; expect: nothing dispatched.
- Stored result already has children (follow-ups were already dispatched); expect: nothing dispatched again.
- Follow-up dispatch fails (broker down) during dedup; expect: error logged, job rejected for requeue, id NOT added to successful set, so it is retried later.
- Backend read fails mid-dispatch (connection lost); expect: rejected with requeue, not marked successful.
- Dispatch raises MemoryError; expect: propagates.
- The reject from dedup failure propagates through the outer run wrapper.
- Dedup disabled (default); expect: redelivered job runs normally.

**Re-entrancy protection (stack protection)**
- Worker optimisations on; a job calls another job function directly; expect: the inner call is treated as a plain call (marked called-directly), not a new tracked job.
- Applying a job from inside a job; expect: a fresh request context with its own args and headers, loglevel reset to 0.
- Calling a job directly from inside a job started via apply; expect: the outer context (args, headers, loglevel) is preserved.

**Message handling**
- Job message with no content type; expect: still executed through both the standard and fast entry points.
- Fast entry point with an empty task registry; expect: clear error "worker task registry is empty".
