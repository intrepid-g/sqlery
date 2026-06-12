"""Filesystem-based deadline contracts between workers and daemon.

Workers write a deadline file before executing a job. The daemon reads
these files each cycle and enforces deadlines by checking actual OS state
(not just DB/purported reality).

Two-phase kill across daemon cycles:
  Cycle N:   overdue → SIGTERM → write .sigterm marker
  Cycle N+1: .sigterm exists → check OS → SIGKILL if needed → reconcile DB
"""

import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.db.models import F
from django.utils import timezone as django_tz

from .models import QueuedJob, Worker

logger = logging.getLogger(__name__)

DEADLINE_DIR = Path("/tmp/sqlery/deadlines")
SIGTERM_SUFFIX = ".sigterm"
DEFAULT_JOB_TIMEOUT = 3600


def _get_default_timeout() -> int:
    return int(os.environ.get("SQLERY_DEFAULT_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT))


# ── Worker-side functions ──────────────────────────────────────────


def write_deadline(worker_id: str, job) -> None:
    """Write a deadline file before executing a job.

    Called by worker_process.py before execute_job().
    """
    DEADLINE_DIR.mkdir(parents=True, exist_ok=True)
    timeout = job.timeout_seconds or _get_default_timeout()
    now = datetime.now(timezone.utc)
    path = DEADLINE_DIR / f"worker-{worker_id}.json"
    path.write_text(json.dumps({
        "job_id": job.id,
        "worker_pid": os.getpid(),
        "timeout_seconds": timeout,
        "started_at": now.isoformat(),
        "deadline": (now + timedelta(seconds=timeout + 1)).isoformat(),
    }))


def clear_deadline(worker_id: str) -> None:
    """Delete the deadline file after job execution completes.

    Called by worker_process.py in the finally block after execute_job().
    """
    path = DEADLINE_DIR / f"worker-{worker_id}.json"
    path.unlink(missing_ok=True)
    # Also clean up any stale sigterm marker
    sigterm_path = path.with_suffix(SIGTERM_SUFFIX)
    sigterm_path.unlink(missing_ok=True)


# ── Daemon-side functions ──────────────────────────────────────────


def enforce_deadlines() -> int:
    """Non-blocking deadline enforcement. Two-phase kill across cycles.

    Phase 1: Overdue deadline, no .sigterm file -> send SIGTERM, write marker.
    Phase 2: .sigterm file exists -> check if dead -> SIGKILL or cleanup.

    Never sleeps. Returns count of jobs enforced.
    """
    # from .models import QueuedJob, Worker  # moved to top-level

    if not DEADLINE_DIR.exists():
        return 0

    now = datetime.now(timezone.utc)
    enforced = 0

    for path in DEADLINE_DIR.glob("worker-*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        sigterm_path = path.with_suffix(SIGTERM_SUFFIX)
        pid = data["worker_pid"]
        job_id = data["job_id"]
        timeout = data["timeout_seconds"]

        # ── Phase 2: We already sent SIGTERM on a previous cycle ──
        if sigterm_path.exists():
            process_alive = _pid_is_sqlery_worker(pid)

            if process_alive:
                # SIGTERM didn't work — escalate to SIGKILL
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    process_alive = False

            _reconcile_overdue_job(QueuedJob, Worker, job_id, pid, timeout, process_alive)
            path.unlink(missing_ok=True)
            sigterm_path.unlink(missing_ok=True)
            enforced += 1
            continue

        # ── Phase 1: Check if deadline is overdue ──
        try:
            deadline = datetime.fromisoformat(data["deadline"])
        except (ValueError, KeyError):
            continue

        if now <= deadline:
            continue

        process_alive = _pid_is_sqlery_worker(pid)

        if not process_alive:
            # Already dead — skip straight to DB reconciliation
            _reconcile_overdue_job(QueuedJob, Worker, job_id, pid, timeout, process_alive=False)
            path.unlink(missing_ok=True)
            enforced += 1
            continue

        # Process is alive but overdue — send SIGTERM, mark for phase 2
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # Died between check and kill
            _reconcile_overdue_job(QueuedJob, Worker, job_id, pid, timeout, process_alive=False)
            path.unlink(missing_ok=True)
            enforced += 1
            continue

        # Write marker so phase 2 picks this up next cycle
        sigterm_path.write_text(datetime.now(timezone.utc).isoformat())
        logger.info(f"Deadline overdue for job {job_id} (pid={pid}, timeout={timeout}s) — SIGTERM sent")

    return enforced


def rebuild_deadlines() -> int:
    """Reconstruct deadline files from DB for running jobs missing them.

    Called once at daemon startup. Closes the gap where deadline files
    are lost (container restart, /tmp cleared) but jobs are still running.
    """
    # from .models import QueuedJob  # moved to top-level

    DEADLINE_DIR.mkdir(parents=True, exist_ok=True)

    # Which workers already have deadline files?
    existing = {
        p.stem.replace("worker-", "")
        for p in DEADLINE_DIR.glob("worker-*.json")
    }

    running_jobs = QueuedJob.objects.filter(
        status='running',
        started_at__isnull=False,
    ).select_related('worker')

    rebuilt = 0
    for job in running_jobs:
        if not job.worker_id:
            continue
        worker_id = str(job.worker_id)
        if worker_id in existing:
            continue

        timeout = job.timeout_seconds or _get_default_timeout()
        deadline = job.started_at + timedelta(seconds=timeout + 1)

        path = DEADLINE_DIR / f"worker-{worker_id}.json"
        path.write_text(json.dumps({
            "job_id": job.id,
            "worker_pid": job.worker_pid or 0,
            "timeout_seconds": timeout,
            "started_at": job.started_at.isoformat(),
            "deadline": deadline.isoformat(),
        }))
        rebuilt += 1

    if rebuilt:
        logger.info(f"Rebuilt {rebuilt} deadline file(s) from DB")
    return rebuilt


def _find_overdue_deadlines() -> list[dict]:
    """Return data dicts for all overdue deadline files."""
    if not DEADLINE_DIR.exists():
        return []

    now = datetime.now(timezone.utc)
    overdue = []

    for path in DEADLINE_DIR.glob("worker-*.json"):
        try:
            data = json.loads(path.read_text())
            deadline = datetime.fromisoformat(data["deadline"])
            if now > deadline:
                overdue.append(data)
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            continue

    return overdue


# ── Utility functions ──────────────────────────────────────────────


def _pid_is_sqlery_worker(pid: int) -> bool:
    """Check if PID exists AND is a sqlery worker (not a recycled PID)."""
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive
        return True

    # Verify it's actually our process (Linux)
    if sys.platform == "linux":
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_text()
            return "sqlery" in cmdline
        except (FileNotFoundError, PermissionError):
            pass

    # macOS or /proc unavailable — trust PID exists
    return True


def _reconcile_overdue_job(QueuedJob, Worker, job_id, pid, timeout, process_alive):
    """Update DB to reflect actual reality after a deadline enforcement."""
    if process_alive:
        outcome = "alive — SIGKILL sent"
    else:
        outcome = "already dead"

    updated = QueuedJob.objects.filter(id=job_id, status='running').update(
        status='failed',
        error=f'Daemon watchdog: exceeded {timeout}s timeout (process was {outcome})',
        termination_reason='daemon_timeout_kill',
        finished_at=django_tz.now(),
        version=F('version') + 1,
    )
    # Old: Worker.objects.filter(...).update(status='dead', current_job=None)
    Worker.objects.filter(pid=pid, status__in=['idle', 'busy']).update(
        status='dead', current_job_id=None,
    )

    if updated:
        logger.info(f"Reconciled overdue job {job_id} (pid={pid}, process {outcome})")
