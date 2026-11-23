"""Party: ZombieSweep. It fails a running job whose worker can no longer run it."""

from datetime import datetime, timedelta

from sqlery.core.liveness import RunningJobLiveness

# The heartbeat may lag the daemon's cycle by timing jitter: stale means 3 timeouts silent.
STALE_FACTOR = 3


def stale_threshold(alive_timeout: float) -> float:
    """Clause ZS-0: how long a worker may stay silent before it counts as unresponsive."""
    return alive_timeout * STALE_FACTOR


def pid_probe_applies(
    worker_pid: int | None, worker_node_id: str | None, current_node: str
) -> bool:
    """Clause ZS-1a: a PID can be probed only on the node that owns it; remote nodes use ZS-5."""
    return bool(worker_pid) and worker_node_id == current_node


def pid_is_gone(rec: RunningJobLiveness, pid_alive: bool | None) -> str | None:
    """Clause ZS-1: the recorded worker PID is gone (``pid_alive`` is None when not probed)."""
    if pid_alive is False:
        return f"Worker PID {rec.worker_pid} no longer exists on {rec.worker_node_id}"
    return None


def unassigned_job_is_zombie(
    rec: RunningJobLiveness, now: datetime, alive_timeout: float, concurrent_workers: bool
) -> str | None:
    """Clause ZS-2: a running job with no worker was never properly claimed.

    An async worker has a claim-to-register window, so a young job gets ``alive_timeout`` grace.
    """
    if rec.has_worker:
        return None
    young = rec.started_at and rec.started_at >= now - timedelta(seconds=alive_timeout)
    if concurrent_workers and young:
        return None
    return "Running job has no worker assigned"


def worker_marked_dead(rec: RunningJobLiveness) -> str | None:
    """Clause ZS-3: the assigned worker is marked dead."""
    if rec.has_worker and rec.worker_status == "dead":
        return f"Assigned worker {rec.worker_friendly_name} is dead"
    return None


def worker_abandoned_job(
    rec: RunningJobLiveness, now: datetime, alive_timeout: float, concurrent_workers: bool
) -> str | None:
    """Clause ZS-4: the worker works on another job, or sits idle, past the claim grace period.

    Skipped for concurrent workers: their single ``current_job`` says nothing about the others.
    """
    if not rec.has_worker or concurrent_workers or rec.worker_current_job_id == rec.job_id:
        return None
    if rec.worker_current_job_id:
        return (
            f"Worker {rec.worker_friendly_name} moved on to job "
            f"#{rec.worker_current_job_id} — this job was abandoned"
        )
    if rec.started_at and rec.started_at < now - timedelta(seconds=alive_timeout):
        age = int((now - rec.started_at).total_seconds())
        return (
            f"Worker {rec.worker_friendly_name} is idle but job has been "
            f"running for {age}s — zombie"
        )
    return None


def heartbeat_is_stale(rec: RunningJobLiveness, now: datetime, alive_timeout: float) -> str | None:
    """Clause ZS-5: the assigned worker has been silent past ``stale_threshold``."""
    threshold = stale_threshold(alive_timeout)
    beat = rec.worker_last_heartbeat
    if rec.has_worker and beat and beat < now - timedelta(seconds=threshold):
        age = int((now - beat).total_seconds())
        return f"Worker {rec.worker_friendly_name} heartbeat stale ({age}s old, threshold {threshold}s)"
    return None


def zombie_verdict(
    rec: RunningJobLiveness,
    *,
    now: datetime,
    alive_timeout: float,
    pid_alive: bool | None,
    concurrent_workers: bool,
) -> str | None:
    """Decision: the reason to fail this running job, or None to leave it. First check wins."""
    return (
        pid_is_gone(rec, pid_alive)
        or unassigned_job_is_zombie(rec, now, alive_timeout, concurrent_workers)
        or worker_marked_dead(rec)
        or worker_abandoned_job(rec, now, alive_timeout, concurrent_workers)
        or heartbeat_is_stale(rec, now, alive_timeout)
    )
