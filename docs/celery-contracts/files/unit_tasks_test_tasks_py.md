### `t/unit/tasks/test_tasks.py`

**Retry count and limits**
- Job with max_retries=3 that always asks to retry; run it; expect 4 executions (1 original + 3 retries), then failure.
- Same job enqueued with a per-call max_retries=10 override; expect 11 executions (call override beats job default).
- Job with no arguments that retries 3 times; expect 4 executions and a successful final result.
- Job retries past its limit with no explicit error; expect a "max retries exceeded" failure that carries the original args and kwargs. Limit 2 gives 3 runs, limit 1 gives 2 runs.
- Job retries with its own custom exception and limit 2; expect 3 runs and the custom exception surfaced as the failure.
- Job with max_retries=0 that retries with an explicit exception; expect 1 run and that exception surfaced.
- Job whose retry sets a lower limit than the job default (default 10, retry says 3); expect 3 runs, then the custom exception.
- Job that retries with an exception that cannot be serialized; expect 3 runs and a safe wrapper failure that keeps the exception class name and args (the error is never lost or crashing the worker).
- Retry that is eager and does not raise; expect it still re-runs and the final return value (42, or the first arg) is the result.
- Retry with throw=False versus throw=True; expect a new run is scheduled in both cases, but only throw=True raises the retry signal to stop the current run.
- Retry with no kwargs given (None); expect it works.

**Retry arguments**
- Retry with new positional args; expect the next run uses them and other original kwargs are kept.
- Retry with new kwargs only; expect they override the matching original parameters and the rest are kept (positional, last positional, variadic, several at once, keyword-only, after positional-only).
- Retry with nothing overridden; expect original args and kwargs are reused unchanged.
- Retry with both new args and kwargs; expect exactly those used.
- Retry kwargs dict passed by the caller; expect it is never mutated by the retry.
- Retry naming a positional-only parameter via kwargs; expect it goes to extra kwargs, the positional value is untouched.
- Retry with kwargs that fit no parameter, or with a function whose signature cannot be inspected (TypeError/ValueError); expect no crash, original args kept and the new kwargs passed through as given.
- Retry creates a new job copy carrying over the original's headers, delivery info (exchange, routing key), priority and shadow (display) name.
- Retry drops dead-letter bookkeeping headers (x-death and x-first/last-death-*) while keeping custom headers, so the broker does not see a cycle.

**Automatic retry on chosen errors**
- Job configured to auto-retry on a listed exception type; expect retries until the limit (4 runs at max 3, plus extra per the configured total), and a non-listed exception type is not retried (1 run).
- Auto-retry settings declared on a shared base class; expect inherited, and a subclass can override the exception list, retry kwargs, backoff, backoff cap and jitter.
- Auto-retry with exponential backoff; expect delays 1,2,4,8 (doubling), a factor of 2 giving 2,4,8,16, True/negative/fractional/1 all behaving as factor 1, and False/0 meaning no delay set.
- Backoff cap set; expect delays double up to the cap then stay at it (1,2,4,8,16,16).
- Backoff with jitter on; expect delay is random between 0 and the computed value (0,1,3,7 under a fixed random source).
- Class-based job with auto-retry settings; expect the same retry counts and delays.
- Settings dict shared by a base class or captured at registration; expect running one job to exhaustion, or a per-call max_retries override, never changes the shared dict, and a sibling job still starts at the first-attempt delay.
- Retry scheduled when the clock is non-UTC with a timezone; expect retries still work (no wrong ETA).

**Calling and enqueueing validation**
- Enqueue with missing required argument, wrong keyword, or unknown extra keyword; expect an immediate TypeError, nothing queued.
- Same wrong calls with strict argument checking turned off (per job or globally); expect they are accepted and queued.
- Enqueue with kwargs not a mapping, or args not a list/tuple; expect TypeError.
- Enqueue with arguments the configured serializer cannot encode (e.g. a set under JSON); expect an encode error up front; with pickle allowed (setting or per job), accepted. Same when run in eager mode.
- Job subclass that does not implement run; expect NotImplementedError.
- Job is directly callable and runs its body; has a name and readable repr (including a "v2 compatible" marker).
- A job object pickled and unpickled resolves to the registered job.
- Job with no app bound picks up the current app on first use.

**What is stored when enqueued**
- Enqueue without args; expect message with job id, name, empty args.
- Enqueue with kwargs; expect they arrive in the message. Same via send-by-name (job not imported).
- Enqueue with ETA and expiry (absolute aware, absolute naive, already in the past, relative seconds) or countdown; expect ETA and expiry stored as ISO timestamps, past expiry still stored.
- Args/kwargs display text: default is the repr of args; caller can override with redacted text (e.g. '***') so secrets stay out of logs.
- Purge queue; expect it returns the count removed (1) and queue empty afterward.
- Result not successful until marked done; after mark-done it is successful.
- Priority resolution: app default priority, job-level priority, base-class priority, per-call; priority 0 is honoured (not treated as unset); a child job inherits the parent priority when that option is on.
- Custom shadow name function sets the display name of the queued message.
- Job with ignore-result flag enqueues with ignore_result=True; per-call ignore_result overrides; default is False; cached execution options are never mutated by a call.
- Declaring routing attributes (queue, exchange, routing_key, delivery_mode, priority...) on the job class emits a deprecation warning.

**Time limits**
- Job takes app-wide hard and soft time limits when it has none; explicit job limits are not overwritten.
- Request context unpacks a (hard, soft) pair, list or tuple, into two fields; None leaves both None.
- Eager run exposes the app-configured limits on its request; with none configured all are None.
- Soft limit greater than hard limit; expect rejection ("soft_time_limit must be less than or equal to time_limit") or failure state.

**Running a job inline (eager)**
- Run job with default, positional and keyword arguments; expect a finished result with the right value, successful, ready, correct name, readable repr.
- Job that raises (KeyError); expect result ready, not successful, traceback stored, getting the value re-raises the error.
- Raise immediately when throw=True or the "eager propagates" setting is on.
- Request context while running: is_eager true, task name set, delivery info simulated (priority, routing key, exchange).
- Job ids: a lone job has no parent and root id equals its own id; nested jobs (parent calls child calls grandchild) get parent_id of caller and root_id of the first; parent without a root id makes the child its own root.
- Request context get/default/clear helpers behave; a job's trail disabled makes add_trail a no-op.

**Job state reporting**
- Job reports custom progress state with metadata for a given id, and for its own request id when none given; state and metadata are readable; extra keyword args are accepted and the request is handed to the result backend.
- Job can send custom events with its id and retry policy.
- A result handle for an id uses the job's backend and that id.
- After-return hook with a chord set runs without error.

**Replacing a job with another (closest to dependencies)**
- Job replaces itself with another job; expect the original run is ended quietly (not marked failed) and the replacement keeps the original id, root id, group/chord info and nesting level.
- Replacement stamped via a custom hook is stamped before sending.
- Replacing while already inside a chord is rejected as a configuration error.
- Original's success and error follow-up jobs are carried to the replacement; for a chain they are attached to the last step exactly once; for a group they are lifted to a chord.
- Chain ending in a group (list, tuple or positional form) becomes a chord whose body accumulates results.
- Replacement works from run(), from run with auto-retry, via delay (returns result handle) and via inline apply (result "replaced").

**Misc**
- Canvas helpers on a job (immutable signature, chunks, map, starmap, on_success) all construct valid objects.
- Job annotations from config are applied to the job.

---
_Generated by [Claude Code](https://claude.ai/code)_
