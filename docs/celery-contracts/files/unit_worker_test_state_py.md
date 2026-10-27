### `t/unit/worker/test_state.py`

**Shutdown signals**
- Stop flag unset (None/False), no terminate flag; worker checks for shutdown; expect: nothing happens, worker keeps running.
- Stop flag set (True); worker checks; expect: graceful shutdown requested.
- Stop flag set to an exit code (e.g. 303); worker checks; expect: process exits with exactly that code.
- Terminate flag set, whatever the stop flag is (unset, false, true, OK code); expect: immediate terminate wins over graceful stop.

**Revoked (cancelled) job memory**
- Merge in nothing (null, empty list, empty dict); expect: cancelled set unchanged, no error.
- Merge in cancelled ids from a list or dict whose timestamps are in the future (clock skew); expect: ids are remembered but stamped with the local current time, never a future time.
- Cancelled set is bounded: max entries (default 50000) and expiry (default 3h); expect: old/excess entries drop out.

**Persisting worker state across restart**
- Close the persistent store twice; expect: second close is a harmless no-op.
- Open a fresh store; expect: empty, bound to the configured file.
- Save; expect: data flushed to disk and store closed.
- Previously cancelled ids exist in memory, load saved state; expect: they remain cancelled after merge.
- Saved state came from a host that has since rebooted (timestamps ahead of the clock); expect: entries are re-stamped locally so they do not live forever.
- Saved state includes a logical clock value; expect: clock is advanced to at least the saved value and still consistent.
- Sync to disk; expect: expired cancelled entries purged first, clock recorded, cancelled set stored compressed, and loadable back with all current ids present (union of saved and in-memory).

**Job lifecycle tracking in the worker**
- Accept 4 jobs (two share a name); expect: all 4 active, per-name totals foo=1, bar=1, baz=2.
- Accept 2 jobs, then mark both finished; expect: active count goes 2 to 0.
- Mark a job scheduled (future run time); expect: tracked as scheduled, not reserved, findable by id.
- Scheduled job becomes reserved; expect: removed from scheduled, present in reserved.
- Scheduled job finishes/cancelled before running; expect: removed from scheduled and forgotten entirely (not findable by id).
- Race: job already reserved (its run time passed and the timer fired early) and then gets marked scheduled; expect: no-op, stays reserved, not re-added to scheduled, still tracked (so shutdown cleanup cannot drop a running job).
- Same race when job is already active; expect: no-op, stays active, still tracked.
- Reset worker state; expect: scheduled set emptied.

**Configuration (limits via environment)**
- Set custom max cancelled/max successful/expiry values by env var; expect: they are used.
- No env vars; expect: defaults (50000, 1000, 10800s, 10800s), expiries remain floats.
- Malformed numeric env var (non-integer for a max, non-float for an expiry); expect: startup fails with a configuration error naming the variable, the expected type and the bad value.
