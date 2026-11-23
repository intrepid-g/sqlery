"""Party: DeadWorkerReaper. It marks silent workers dead so they stop looking alive."""

from datetime import datetime, timedelta

DAEMON_PID = 0  # a daemon heartbeat row, never a worker
# The reaper's own silence limit. It does not follow WORKER_ALIVE_TIMEOUT (30s): see the
# Code-as-contract finding in the commit message (is_beating and the reaper disagree).
REAP_SILENCE_SECONDS = 60


def is_silent_past(
    status: str | None,
    pid: int | None,
    last_heartbeat: datetime | None,
    now: datetime,
    threshold_seconds: float,
) -> bool:
    """Clause DWR-1: a non-daemon worker not yet dead whose last beat is older than the limit."""
    if pid == DAEMON_PID or status == "dead" or last_heartbeat is None:
        return False
    return last_heartbeat < now - timedelta(seconds=threshold_seconds)


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


def reap_verdict(
    *,
    status: str | None,
    pid: int | None,
    last_heartbeat: datetime | None,
    now: datetime,
    pid_exists: bool | None,
) -> str | None:
    """Decision: why to mark this worker dead, or None to leave it.

    ``pid_exists`` is None unless the worker runs on this node, where a missing process is
    proof of death even while its last beat is still recent.
    """
    if is_silent_past(status, pid, last_heartbeat, now, REAP_SILENCE_SECONDS):
        age = int((now - last_heartbeat).total_seconds())
        return f"heartbeat too old ({age}s > {REAP_SILENCE_SECONDS}s threshold)"
    if process_gone_on_this_node(status, pid, last_heartbeat, pid_exists):
        return "process not found on this node"
    return None


def safety_net_action(heartbeat_age: float, stale_after: float, process_exists: bool) -> str:
    """Clause DWR-2: 'keep', 'mark_dead' (process gone) or 'kill' (alive but unresponsive)."""
    if heartbeat_age <= stale_after:
        return "keep"
    return "kill" if process_exists else "mark_dead"
