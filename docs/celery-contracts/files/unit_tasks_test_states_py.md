### `t/unit/tasks/test_states.py`

**Job state precedence (which status wins when two reports disagree)**
- A job reported done outranks the same job reported queued/pending; expect: done wins, so a late "pending" never overwrites "done".
- A job reported failed outranks one reported received/claimed by a worker; expect: failed wins.
- A job reported cancelled (revoked) outranks one reported running/started; expect: cancelled wins.
- A job in an unknown custom state (e.g. "crashed") ranks below done and below failed; expect: terminal done/failed states always win over it.
- Order is symmetric: the lower-ranked state always compares as lower (pending < done, claimed < failed, running < cancelled, crashed < done, crashed < failed).
- An unknown custom state ranks equal to cancelled; expect: neither outranks the other (ties allowed, comparison is >= and <= both true).
- Cancelled is not above an unknown custom state; expect: cancelled <= custom state.
- Portable guarantee: state ordering is total and deterministic, so out-of-order or duplicate status updates (retries, races between worker and monitor) resolve to the most advanced state and never regress a finished job.
