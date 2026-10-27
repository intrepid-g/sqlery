### `t/unit/worker/test_request.py`

Covers the worker-side life of a single job (claim, run, ack/reject, success, failure, timeout, revoke, worker crash).

**Running a job and storing results**
- Run a job that succeeds; expect: return value returned, result stored as done with that value.
- Run a job that raises; expect: result stored as failed with the original exception, error info returned to the worker, worker not crashed.
- Run a job whose result-store cleanup step fails; expect: job still reported done with the right value, the cleanup error logged.
- Run a job when cleanup raises a process-exit signal (SystemExit); expect: it propagates, not swallowed.
- Run a job flagged "ignore result" (on the job definition, or on that one enqueue call); expect: job runs and returns, nothing stored, status never becomes ready.
- Job with "track started" on: expect a started state recorded; with ignore-result also on, expect no started state.
- Job that fails with ignore-result on: expect failure NOT stored by default, but stored if "store errors even if ignored" is set; a per-enqueue ignore_result=False overrides the job default and re-enables error storing; a null per-enqueue value falls back to the job default.
- Retry/failure handling with store_errors off; expect status stays pending (nothing stored); with it on, expect retry or failure status stored.
- A job that raises an unexpected error inside the runner itself (bypassing normal handling); expect a runtime warning and an error info result, not a crash.
- Job run with a result backend that errors on both "mark done" and "mark failed" while acks-late; expect the message is rejected (not acked), so it is not lost.
- Run job then check the payload sent to the pool; expect task name, id, request headers and body passed through unchanged, with the right callbacks (accepted, success, failure, timeout) and the job's soft/hard time limits.
- A null time-limit header; expect the job's configured limits used instead.
- Fast-path vs normal execution path both give the same stored string result and a numeric runtime.

**Bad input**
- Job with a malformed ETA or malformed expiry; expect rejected as an invalid job.
- Job whose arguments are not a valid structure (kwargs a plain string); expect an invalid-job error, not a crash.
- Job message missing required header fields; expect a key error at construction.
- Job naming an unregistered task; expect an error at construction.
- Unicode and non-ASCII argument names and error messages (including utf-8 bytes); expect handled, failure events still emitted.
- Job with empty args and kwargs; expect valid.
- Expiry with a plain timezone-naive time or aware time; expect treated correctly (naive made timezone-aware).
- Custom job display name (shadow) honoured; absent, the real task name used.
- Large arguments in a status/info view: safe mode returns a truncated repr, unsafe returns full values.
- Job id, name, group id, group index exposed, with old aliases (task_id, task_name) still working both ways.

**Expiry**
- Job with no expiry; expect never expired.
- Job past its expiry; expect marked revoked/expired, recorded in the revoked set, revoked status stored, revoke signal fired with expired=true.
- Job not yet expired; expect not revoked, status untouched.
- Expired job with ignore-result; expect revoked but no status stored.
- Expiry checked across a daylight-saving fall-back hour: valid until the second occurrence, expired once past it, exactly at expiry is NOT expired, one microsecond after IS expired.
- Dispatching an expired or revoked job to the pool; expect a revoked error instead of executing.

**Revocation (cancel before run)**
- Revoked job id on the revoked list; expect revoked check true, acknowledged, revoke signal fired, never executed.
- Already-revoked flag set; expect immediate true without re-checking.
- Revoke by stamp (custom label on a job): single value, list, tuple, set, mixed types; expect revoked and acknowledged.
- With several stamps, revoke when a later stamp matches though an earlier one does not; when only one is flagged and matches; when a list-valued stamp contains the flagged value.
- No stamp matches; expect not revoked.
- A flagged stamp with no value on the job (missing); expect no error, not revoked; and a later valid stamp still revokes.

**Terminate / cancel while running**
- Terminate a running job; expect the pool signalled with the worker pid, revoke signal fired with terminated=true and the signal number; if the pool result handle is already gone, no error.
- Terminate a job still reserved but not started; expect nothing signalled now, the termination remembered and applied once the job is accepted; repeating it is harmless.
- Cancel a running job; expect pool signalled and a retry signal fired; cancel a reserved job; expect no signal and nothing remembered.
- Cancel with "emit retry" on: expect retry status stored, retry hook called, job marked cancelled; with it off: killed but no retry status or hook.
- Failure caused by our own terminate/cancel (terminated error, or the child's SystemExit from our signal) on a cancelled or revoked job; expect no failure event, no failure stored, message left unacked so it is redelivered.
- Failure reported while the worker itself is shutting down; expect no event, no failure stored.
- Terminated failure; expect a revoked event with terminated=true.

**Acknowledgement (early vs late)**
- Default early ack: acknowledged when the job is accepted by a pool process; late ack: not acknowledged on accept, acknowledged after success or failure.
- Ack is idempotent; reject after ack is a no-op (second reject does nothing).
- Success with early ack vs late ack behaves accordingly; logging off does not change ack state.
- Late-ack job that fails normally; expect acknowledged.
- Failure with "ack on failure" setting: ack when true; when false, message rejected without requeue (dead-lettered) yet marked acknowledged; independent of the timeout setting, and vice versa.
- Combined legacy flag (ack on failure or timeout) still works, both true and false, and resolves into the two separate flags.
- Mixed policy: acknowledge failures but requeue timeouts.
- Retry with late ack; expect acknowledged on retry.

**Success handling**
- Success with events enabled; expect a succeeded event with result and runtime; no event when events are disabled for the worker or the job.
- Success callback received an error result from the pool; expect routed to the failure path (with return_ok).
- Success callback with a process-exit exception (SystemExit, KeyboardInterrupt); expect propagated, not swallowed.
- Success callback with out-of-memory; expect propagated with an explanatory message.
- Memory error in failure handling; expect propagated.

**Failure handling**
- Any failure; expect a failure event emitted, failure stored, failure signal fired with the request visible to handlers and the previous request context restored afterwards.
- Failure stored with the exact exception, including WorkerLost.
- Failure event for non-English and utf-8 messages works.
- Job asks to be ignored (Ignore); expect acknowledged, nothing stored.
- Job asks to be retried (Retry); expect a retried event with exception and traceback, no crash if info logging is off.
- Job rejects itself without requeue (Reject); expect rejected not requeued, a terminal failure stored, failure signal fired, failure event sent (unless failed events are turned off, then still stored but no event), also stored when the message was already acked early.
- Job rejects itself with requeue; expect requeued, nothing stored, no failure event (it will run again).
- Task exits the process itself (SystemExit with code, none, or text; KeyboardInterrupt); expect treated as worker lost with message "Worker exited prematurely: exitcode N / KeyboardInterrupt", acked when late; with reject-on-worker-lost, requeued instead and nothing stored.

**Worker crash (worker lost)**
- Worker process dies mid-job; expect failure stored (status failed) unless ignore-result, in which case stays pending.
- Late ack plus reject-on-worker-lost: job requeued, no failure stored, whether or not it was already redelivered (including redelivered unknown); a redelivered job on early-ack still gets failure stored.
- Retry-on-worker-lost: job is re-enqueued with retry count incremented and the configured delay, retry status stored, retried event and retry hook and retry signal fired, original acked not rejected.
- Retries exhausted: job fails instead of being re-enqueued, acked.
- Re-enqueue itself fails (queue down): falls back to reject-and-requeue so the job is not lost; if reject-on-worker-lost is off, failure stored instead.
- Retry-on-worker-lost with early ack: re-enqueued, no extra ack.
- Result already stored as success before the worker died (child finished then crashed): expect success kept, no failure stored, no error callbacks fired, no requeue or retry, a succeeded event sent with the result, message acked.
- That success check is skipped when result is ignored (job or per-call) or the backend cannot be queried (rpc style); if the backend lookup itself errors, expect fall back to treating it as failure.

**Time limits**
- Hard timeout: expect error logged "Hard time limit", failure (TimeLimitExceeded) stored; with late ack acked, early ack untouched.
- Hard timeout with ack-on-timeout true: acked; false: rejected with requeue, not acked; ack-on-failure setting does not affect it.
- Timeout failure also calls the job's failure hook, fires failure signal, sends a failed event, and calls after-return hook with FAILURE and the timeout exception.
- Timeout error leaves no traceback reference on the stored exception (avoid memory leak from retained job state).
- Soft timeout: expect warning logged, nothing stored (still pending), not acked, ignore-result unaffected.
- Timeout callback while worker is shutting down; expect nothing logged or done.

**Events and signals**
- Send event includes the job id; suppressed if the job disables events.
- Revoke, retry, failure signals carry the job, request context and flags.
- Info view shows args, kwargs, name; repr of request is non-empty.

**Pool dispatch**
- Dispatch keeps only a weak reference to the pool result so finished jobs can be freed.
- Default header values from older message format work.
- Method resolution helper picks the nearest class defining an attribute, honours a stop set, and returns none when ancestry is empty.

---
_Generated by [Claude Code](https://claude.ai/code)_
