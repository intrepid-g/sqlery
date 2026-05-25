"""Framework-agnostic data structures for daemon liveness checks.

The daemon's zombie-detection logic operates on plain structured records so it
can run identically against either integration backend (Django ORM or
SQLAlchemy/SQLModel). Each backend builds these records from its own models;
the decision logic in ``daemon._fail_zombie_running_jobs`` never touches an ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
