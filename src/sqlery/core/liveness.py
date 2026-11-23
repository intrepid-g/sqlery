"""Framework-agnostic data structures for daemon liveness checks.

The daemon's zombie-detection logic operates on plain structured records so it
can run identically against either integration backend (Django ORM or
SQLAlchemy/SQLModel). Each backend builds these records from its own models;
the decision logic in ``daemon._fail_zombie_running_jobs`` never touches an ORM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from sqlery.contract.config import misconfiguration
from sqlery.contract.dead_worker_reaper import probe_found_process

logger = logging.getLogger(__name__)


@dataclass
class RunningJobLiveness:
    """A snapshot of a single ``status='running'`` job plus its worker.

    All datetimes are timezone-aware UTC. When the job has no worker assigned,
    ``has_worker`` is False and every ``worker_*`` field is None.

    Attributes:
        job_id: Primary key of the running job.
        started_at: When the job began executing (tz-aware UTC) or None.
        worker_pid: OS PID recorded on the job, or None.
        worker_node_id: node_id of the assigned worker, or None.
        worker_status: Assigned worker's status (idle/busy/dead), or None.
        worker_current_job_id: Job id the worker currently claims, or None.
        worker_last_heartbeat: Worker's last heartbeat (tz-aware UTC) or None.
        worker_friendly_name: Human-friendly worker name for logging, or None.
        has_worker: True if a worker row is assigned to this job.
    """

    job_id: int
    started_at: datetime | None
    worker_pid: int | None
    worker_node_id: str | None
    worker_status: str | None
    worker_current_job_id: int | None
    worker_last_heartbeat: datetime | None
    worker_friendly_name: str | None
    has_worker: bool


def check_liveness_config(heartbeat_interval: float, alive_timeout: float) -> None:
    """Edge: warn once at startup when the heartbeat cannot keep a worker alive (CFG-1).

    A warning, not an error: a deploy that runs today must keep running.
    """
    problem = misconfiguration(heartbeat_interval, alive_timeout)
    if problem:
        logger.warning(problem)


def pid_exists(pid: int) -> bool:
    """Edge: probe a PID with signal 0 and let clause DWR-3 read the outcome."""
    try:
        os.kill(pid, 0)
    except OSError as e:
        return probe_found_process(e)
    return True
