### `t/smoke/tests/test_control.py`

**Worker liveness and control**
- Ping all running workers; expect every worker replies "pong".
- Send a shutdown command to one named worker; expect the worker process exits and its exit code is 0 (clean shutdown, not a crash).
- Ask workers for a status report; expect every worker answers, and the report includes version/software information.

**Secrets never leak into diagnostics**
- Generate a bug/diagnostic report with a multi-server result store URL; expect the URL (hosts and ports) appears in the report.
- Generate a report where the store URL carries passwords (multi-host sentinel style, one password per host); expect no password appears and each is masked as "********", the rest of the URL intact.
- Passwords containing commas or "@" characters (with or without a username); expect they are fully masked, username and host preserved.
- Restore the original configuration afterwards even if assertions fail.

**Pending control operations keep their order**
- Single-slot worker; send "add a queue consumer, then cancel that consumer" as scheduled operations; expect the worker acknowledges both as scheduled.
- Poll until the worker's pending-operations count is 0 (drained); expect it drains within a bounded wait.
- After draining, expect the queue is NOT among the worker's active queues (cancel was applied after add, not reordered or lost).

**Revoking a job that is waiting inside a dependent group**
- Single-slot worker; enqueue a group of 2 jobs (job A slow, job B quick) with a follow-up job that runs after both; expect A running and B reserved (waiting).
- Revoke B while it is reserved; expect the worker logs that it discards the revoked job once A finishes.
- Expect the group does not hang: it reaches a terminal state, with the follow-up job failing with a "revoked" error because B's stored state is REVOKED.
- Expect B stored as revoked, and A still ran to completion with its result intact.
