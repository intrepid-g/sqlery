### `t/integration/test_tasks.py`

**Basics and arguments**
- Call a job function directly (no queue); expect it runs inline and returns the value, kwargs honoured.
- Enqueue 10 jobs with positional args, run the worker; expect 10 done with correct results, each reporting success/ready/successful.
- Same with keyword args; expect correct results.
- Call or enqueue with missing or unknown arguments; expect an immediate TypeError at call time and nothing enqueued.
- Same bad arguments on a job that opted out of argument checking; expect it is enqueued, the worker runs it, and it ends failed with TypeError.
- Enqueue jobs with unicode output (5 at once); expect all succeed.
- Enqueue with custom message properties (e.g. app id); expect the job sees them.
- (Skipped upstream, intent) 20 producer processes or threads each enqueue 20 jobs; expect every job returns its own correct result.

**Results and errors**
- Enqueue a job with ignore-result; expect it runs, result is None, nothing stored.
- Wait for the result of a 10s job with a 5s timeout; expect a timeout error for the waiter only, job unaffected.
- Enqueue a job that raises; expect the original exception type reaches the caller, status failed, ready, not successful.
- Job fails with an unpicklable exception; expect the error is still retrievable (class name and args preserved), status failed.
- Job retries with an unpicklable exception; expect status retry, error info readable (class name and args), not yet completed; then revoke it.

**Reject**
- Job rejects without requeue; expect status failed, ready, error is the reject error.
- Job rejects with requeue; expect it is redelivered and succeeds on the second pass.

**Expiry and delay**
- Fill the queue with 4 slow jobs, enqueue a job expiring in 1s; expect it is skipped, status revoked, ready, neither failed nor successful.
- Same with an absolute expiry time; expect same outcome.
- Enqueue with a 3s delay; expect pending and not ready at 1s, done only after 3s, then success.
- Same with an absolute run-at time; expect same outcome.

**Revoke / cancel**
- Fill the queue, enqueue a job and revoke it before it runs; expect revoked, not failed.
- Revoke a job that another worker has already started (state started/progress) and one never started; expect the started one keeps its state, the unstarted one becomes revoked, and the revoke is acknowledged.
- Stamp a job with a tag, revoke by tag; expect matching job revoked, jobs with other tag values (other string, number, none) still run to success; clear tags between rounds.
- Revoke by tag when the stamped job sits inside group, chord, chain, or nested combinations (dependencies); expect it is revoked wherever it sits, tag value as string or list.
- Revoke by tag matching nothing; expect reply "flagged as revoked, but not terminated".

**Retries**
- Job that always retries; expect status retry is visible, then failed with the final error once max retries is reached.
- Job that retries then succeeds; expect retry state seen, final success with the value.
- Retry switching from kwargs to args (and the reverse); expect updated arguments used, other values kept, retry count 1, not eager.
- Job retried once; expect exactly one retry in the result.
- Retried job enqueued with expiry 60; expect it still completes after retry.
- Retry preserves priority (7 in, 7 out).
- Retry preserves custom headers.
- Class-based job with auto-retry; expect it retries and finishes successfully.

**Time limits**
- Soft limit greater than hard limit; expect a "soft_time_limit must be <= time_limit" error.
- Limits passed at enqueue; expect the job sees them.
- No limits configured; expect both are None.
- Limits declared on the job definition; expect the job sees them.
- Enqueue-time limits vs declared; expect enqueue-time wins.

**Nested inline runs**
- Run a single job inline; expect no parent, root id equals own id.
- Parent runs child runs grandchild inline; expect each parent id is the caller's id and all share the top root id.

**Logging**
- Successful job with custom args/kwargs display text; expect a "received" line and a "success" line (info) using that text.
- Failing job; expect "received" (info) then "failure" (error) lines with that text.

**Result-store subscriptions (Redis-specific; portable: no leaked listeners)**
- Ignored-result job; expect no result subscription opened.
- Result forgotten before completion; expect subscription cancelled.
- Result fetched; expect subscription cancelled afterwards.

**Job replacement**
- Job replaces itself 0, 1, 3 times; expect nesting levels 0..N recorded in order.
- Ordinary job; expect nesting level 0.
- Job replaced by a job that replaces another; expect execution order In A, In B, In/Out C, Out B, Out A, nesting bounded.
- Same replacement inside a chain, followed by another step; expect same order and the following step runs.

**Signals / hooks**
- Two listener instances registered on publish and receive hooks; expect both see the job id on both events.
- Disconnect one listener; expect the other still fires, the disconnected one sees nothing.
- One listener garbage-collected; expect the other still fires.

**Publishing and concurrency**
- Enqueue with an acquire timeout configured; expect normal success.
- Default (no) timeout; expect enqueue blocks as needed and succeeds.
- 50 enqueues from 20 threads with a 10s timeout; expect none blocks, all complete with results.
- Sent-event publish fails with retries set to 0; expect no retry, job still completes, the event is buffered once for later.
- (Skipped upstream, intent) Two slow jobs queued; expect the first is reported as accepted (claimed) by the worker.

---

_Generated by [Claude Code](https://claude.ai/code)_
