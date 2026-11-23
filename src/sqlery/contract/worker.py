"""Party: Worker. It must prove it is alive, and must not fake life when it releases a job."""

# A release is one UPDATE that writes these columns and no ``last_heartbeat`` (#35, #37):
# a silent worker keeps its stale beat so the dead-worker reaper still sees it.
RELEASE_FIELDS = {"status": "idle", "current_job_id": None}
RELEASE_STATEMENTS = 1

# A beat is proof of life, so it takes a worker out of ``dead`` (#41). The async backends
# write this as one CASE in their single heartbeat UPDATE, from these two constants.
DEAD_STATUS = "dead"
REVIVED_STATUS = "idle"


def release_fields() -> dict:
    """Clause WRK-1: the columns a worker writes to itself when a job finishes."""
    return dict(RELEASE_FIELDS)


def beat_owed(seconds_since_last_beat: float, interval: float) -> bool:
    """Clause WRK-2 (obligation): the worker owes a heartbeat once ``interval`` has passed."""
    return seconds_since_last_beat >= interval


def idle_tick_interval(lease_secs: float, heartbeat_interval: float) -> float:
    """Clause WRK-3 (obligation): the longest an idle worker may go between ticks.

    A tick renews the scheduler lease and beats, so it comes at least every half lease TTL
    and at least every heartbeat interval (``0`` or less means no heartbeat cap).
    """
    interval = lease_secs / 2
    if heartbeat_interval and heartbeat_interval > 0:
        interval = min(interval, heartbeat_interval)
    return max(interval, 0.1)


def async_beat_interval(heartbeat_interval: float, poll_interval: float) -> float:
    """Clause WRK-4 (obligation): an async worker beats once per poll cycle at most."""
    return max(min(heartbeat_interval, poll_interval * 0.9), 0.0)


def status_after_beat(status: str) -> str:
    """Clause WRK-5: a beat restores the worker's status (idle or busy), never leaves it dead."""
    return REVIVED_STATUS if status == DEAD_STATUS else status
