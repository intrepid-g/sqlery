### `t/unit/app/test_trace.py`

Scope: this file covers one guarantee, that a failed job does not keep its working memory alive. After a job raises, the worker releases the local variables held by the failure's stack frames. The stored traceback stays, so the failure can still be reported.

**Memory release after failure**
- Job raises from nested calls, with large local variables in each call; worker clears the failure it was handed; expect: every frame in the traceback has no locals left.
- Same case; expect: the traceback itself is still attached to the failure, so the error and traceback are still reportable and stored on the failed job.
- Worker clears the failure while still handling it, without passing it explicitly; expect: it uses the failure currently being handled and clears that one.
- Worker clears with an explicit "no failure" value while a failure is being handled; expect: same fallback to the current failure, no error.

**Robustness of the cleanup**
- Clear a failure that was never raised, so it has no traceback; expect: no error, nothing to do.
- A frame is still executing and cannot be cleared; expect: the cleanup swallows it and does not crash. A cleanup problem must never turn a failed job into a worker crash.

**Open gap**
- The file asserts nothing about retries, timeouts, duplicates, or ordering. Those belong to other files.
