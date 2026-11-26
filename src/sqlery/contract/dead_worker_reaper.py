"""Party: DeadWorkerReaper. It marks silent workers dead so they stop looking alive."""

from datetime import datetime, timedelta

DAEMON_PID = 0  # a daemon heartbeat row, never a worker
# Old: REAP_SILENCE_SECONDS = 60, a second "dead" limit that ignored WORKER_ALIVE_TIMEOUT (#40).
# Now WORKER_ALIVE_TIMEOUT is the only rule for dead; both reapers and ``is_beating`` share it.


def is_silent_past(
    status: str | None,
    pid: int | None,
    last_heartbeat: datetime | None,
    now: datetime,
    threshold_seconds: float,
) -> bool:
    """Clause DWR-1: a non-daemon worker not yet dead whose last beat is at least the limit old.

    The boundary matches Clause DB-1: a beat exactly ``limit`` old is no longer alive.
    """
    if pid == DAEMON_PID or status == "dead" or last_heartbeat is None:
        return False
    # Old: last_heartbeat < now - limit (spared a beat exactly at the limit; DB-1 calls it dead)
    return last_heartbeat <= now - timedelta(seconds=threshold_seconds)


def process_gone_on_this_node(
    status: str | None, pid: int | None, last_heartbeat: datetime | None, pid_exists: bool | None
) -> bool:
    """Clause DWR-1b: a live-looking worker whose process is missing on this node is dead."""
    return (
        pid_exists is False
        and pid != DAEMON_PID
        and status != "dead"
        and last_heartbeat is not None
    )


def silence_since(last_heartbeat: datetime | None, watched_since: datetime | None):
    """Clause DWR-4: silence counts only while the watcher could reach the database.

    A worker cannot beat while the database is down, so after an outage every beat looks old.
    The watcher (the daemon) passes when it last regained the database; a beat older than
    that counts from then. ``watched_since`` None means no outage is known (bare workers).
    """
    if last_heartbeat is None or watched_since is None:
        return last_heartbeat
    return max(last_heartbeat, watched_since)


def left_to_safety_net(pid_exists: bool | None, started_by_pool: bool) -> bool:
    """Clause DWR-1c: a live process this pool started is not just marked dead on silence.

    Marking it dead would fail its job (clause ZS-3) while the process, and its job child,
    keep running. The safety net kills it first (clause DWR-2), then marks it dead (#48).
    """
    return bool(pid_exists) and started_by_pool


def reap_verdict(
    *,
    status: str | None,
    pid: int | None,
    last_heartbeat: datetime | None,
    now: datetime,
    pid_exists: bool | None,
    alive_timeout: float,
    watched_since: datetime | None = None,
    started_by_pool: bool = False,
) -> str | None:
    """Decision: why to mark this worker dead, or None to leave it.

    ``alive_timeout`` is WORKER_ALIVE_TIMEOUT, the one limit for dead (Clause DB-1 agrees).
    ``pid_exists`` is None unless the worker runs on this node, where a missing process is
    proof of death even while its last beat is still recent. ``watched_since`` and
    ``started_by_pool`` come from a daemon's pool (clauses DWR-4 and DWR-1c).
    """
    if left_to_safety_net(pid_exists, started_by_pool):
        return None
    last_heartbeat = silence_since(last_heartbeat, watched_since)
    if is_silent_past(status, pid, last_heartbeat, now, alive_timeout):
        age = int((now - last_heartbeat).total_seconds())
        return f"heartbeat too old ({age}s > {alive_timeout:g}s threshold)"
    if process_gone_on_this_node(status, pid, last_heartbeat, pid_exists):
        return "process not found on this node"
    return None


def probe_found_process(probe_error: OSError | None) -> bool:
    """Clause DWR-3: a ``kill(pid, 0)`` probe finds a process unless it says no such process.

    Permission denied means the process exists under another user, so it is alive (#40).
    """
    return not isinstance(probe_error, ProcessLookupError)


def safety_net_action(
    heartbeat_age: float, stale_after: float, process_exists: bool, started_by_pool: bool = True
) -> str:
    """Clause DWR-2: 'keep', 'mark_dead' (process gone) or 'kill' (alive but unresponsive).

    Only a process the pool started is killed: the PID of any other row may have been reused
    by an unrelated process (#48).
    """
    if heartbeat_age <= stale_after:
        return "keep"
    if not process_exists:
        return "mark_dead"
    return "kill" if started_by_pool else "keep"
