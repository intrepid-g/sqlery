### `t/unit/worker/test_strategy.py`

**Legacy message conversion**
- Legacy message with no positional args; expect: job runs with empty args, kwargs kept.
- Legacy message with no kwargs; expect: args kept, empty kwargs.
- Legacy message whose kwargs is not a mapping; expect: rejected as an invalid job.
- Legacy message with no group id; expect: still converts fine.
- Full legacy message; expect: normalised to current format (args, kwargs, no callbacks/chain/chord), group id carried over, original fields preserved.
- Message with no retry count; expect: retries defaults to 0.
- Message with a retry count of 3; expect: retry count preserved.
- Message with custom headers; expect: custom headers preserved after conversion.

**Receiving a job**
- Receive a normal job; expect: reserved for immediate execution, worker notified, "received" event emitted.
- Same job in every message format, with and without UTC timestamps; expect: identical behaviour.
- Receive a job with completion callbacks; expect: each callback called with the job request.
- Receive a job; expect: one info log with id, name, args, kwargs, eta (eta empty when immediate).
- Receive a scheduled job; expect: log shows its eta.
- Custom receive-log format configured; expect: used, with id/name/args/kwargs/eta available.
- Job carries custom args/kwargs display text (to hide secrets); expect: logs show that text, not real values.
- Logging disabled; expect: no log records produced.
- Receive a job; expect: "task received" signal fired exactly once with the request.
- Events disabled; expect: job still reserved and run, no event sent.

**Scheduled (eta/countdown) jobs**
- Job with countdown 10s; expect: scheduled for later, not run now, prefetch slot accounted for.
- Scheduled job before its time; expect: already findable in the known-jobs registry but not in the reserved set.
- Scheduled job; expect: its timer entry is attached before it becomes visible as scheduled (so a concurrent shutdown can always cancel it).
- Scheduled job with non-UTC timestamps; expect: still scheduled correctly.

**Rate limits**
- Job type limited to 1/min; expect: job deferred to the rate limiter, not reserved.
- Rate-limited job that also has an eta; expect: scheduled, then rate-limited after eta, slot accounted.
- Rate limits disabled globally; expect: job reserved immediately.

**Revoked / bad input**
- Job already revoked; expect: discarded on receipt, never run.
- Eta cannot be converted (overflow or invalid value); expect: message rejected without requeue, job not executed.
- Same bad eta; expect: failure recorded in the result store with the exception, not left pending forever.
- Same bad eta with result storage disabled; expect: still rejected, no crash.

**Extensibility**
- Job type configures a custom request class; expect: custom class instantiated for received jobs.
