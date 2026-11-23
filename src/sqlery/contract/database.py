"""Party: Database (both backends). It derives liveness on read and fails zombies once."""

from datetime import datetime

# A running job is a ``queued`` row with an open attempt (#157): a claim writes no job row.
RUNNING_ROW_STATUS = "queued"


def is_beating(
    status: str | None,
    last_heartbeat: datetime | None,
    now: datetime,
    timeout_seconds: float,
) -> bool:
    """Clause DB-1: a worker is alive when it is not 'dead' and beat within the timeout.

    Derived at read time, so it is right with or without a reaper running.
    """
    if status == "dead" or last_heartbeat is None:
        return False
    return (now - last_heartbeat).total_seconds() < timeout_seconds


def can_be_running(job_status: str) -> bool:
    """Clause DB-2: only a ``queued`` row can be a running job; others are left alone."""
    return job_status == RUNNING_ROW_STATUS


def closed_the_attempt(rows_closed: int) -> bool:
    """Clause DB-3: closing the open attempt arbitrates recovery against a completion.

    Zero rows closed means another actor won: the zombie failure must not be written.
    """
    return rows_closed >= 1
