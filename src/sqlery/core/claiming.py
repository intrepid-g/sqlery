"""Framework-agnostic job claiming algorithm.

Promoted from django_sqlery/worker_claiming.py. The orchestration logic
(loop over candidates, check concurrency, check rate limits, check deps,
claim) is pure algorithm parameterized by backend calls.

All database operations are delegated to the DatabaseBackend ABC.
"""

import logging
import os
import socket
from datetime import datetime, timezone

from sqlery.core.utils import parse_rate_limit
from sqlery.core.registry import track_job_start, track_job_finish

# Optional Django imports — only needed by the legacy release_job() helper
# which is preserved here for the django_sqlery worker_process.py runner.
try:
    from django.db import transaction as _django_transaction
    from django.db.models import F as _django_F
    from django.utils import timezone as _django_timezone
except ImportError:
    _django_transaction = None
    _django_F = None
    _django_timezone = None

logger = logging.getLogger(__name__)

__all__ = [
    "get_node_id",
    "check_tag_concurrency_limits",
    "check_tag_rate_limits",
    "check_job_dependencies",
    "expire_ttl_jobs",
    "claim_next_job_with_queue_priority",
    "release_job",
]


def get_node_id():
    """Get the node identifier (hostname or NODE_ID env var)."""
    return os.environ.get("NODE_ID", socket.gethostname())


def check_tag_concurrency_limits(job, tag_limits: dict, backend) -> bool:
    """Check if job's tags would exceed configured concurrency limits.

    Args:
        job: Job instance with tags field
        tag_limits: Dict mapping tag -> max concurrent jobs
        backend: DatabaseBackend instance

    Returns:
        True if job can run, False if tag limits would be exceeded
    """
    if not getattr(job, 'tags', None):
        return True

    if not tag_limits:
        return True

    for tag in job.tags:
        limit = tag_limits.get(tag)
        if limit is None:
            continue

        running_count = backend.count_running_with_tag(tag)
        if running_count >= limit:
            return False

    return True


def check_tag_rate_limits(job, tag_rate_limits: dict, backend) -> bool:
    """Check if job's tags would exceed configured rate limits.

    Rate limits control how many jobs can START within a time window.

    Args:
        job: Job instance with tags field
        tag_rate_limits: Dict mapping tag -> rate limit string (e.g., "60/m")
        backend: DatabaseBackend instance

    Returns:
        True if job can run, False if rate limits would be exceeded
    """
    if not getattr(job, 'tags', None):
        return True

    if not tag_rate_limits:
        return True

    # from datetime import datetime, timezone  # moved to top-level

    for tag in job.tags:
        rate_limit_str = tag_rate_limits.get(tag)
        if not rate_limit_str:
            continue

        try:
            count, time_window = parse_rate_limit(rate_limit_str)
        except ValueError as e:
            logger.error(f"Invalid rate limit for tag '{tag}': {e}")
            continue

        now = datetime.now(timezone.utc)
        threshold = now - time_window

        started_count = backend.count_started_with_tag_since(tag, threshold)

        if started_count >= count:
            logger.debug(
                f"Rate limit exceeded for tag '{tag}': {started_count}/{count} "
                f"started in last {time_window.total_seconds()}s"
            )
            return False

    return True


def check_job_dependencies(job) -> bool:
    """Check if all job dependencies have completed successfully.

    If any dependency has failed, marks this job as failed.
    If dependencies are not yet complete, returns False to skip claiming.

    Args:
        job: Job instance with dependencies field and check_dependencies_met() method

    Returns:
        True if job can run (dependencies met), False if should skip
    """
    if not getattr(job, 'dependencies', None):
        return True

    all_met, failed_deps = job.check_dependencies_met()

    if failed_deps:
        job.mark_failed(
            error=f"Dependencies failed: {failed_deps}",
            termination_reason="dependency_failed"
        )
        logger.info(f"Failed job {job.id} due to failed dependencies: {failed_deps}")
        return False

    if not all_met:
        logger.debug(f"Skipping job {job.id} - dependencies not yet complete")
        return False

    return True


def expire_ttl_jobs(backend) -> int:
    """Mark queued jobs as failed if their TTL has expired.

    Args:
        backend: DatabaseBackend instance

    Returns:
        Number of expired jobs
    """
    expired_jobs = backend.get_expired_ttl_jobs()

    expired_count = 0
    for job in expired_jobs:
        job.mark_failed(
            error=f"Job expired after {job.ttl}s in queue",
            termination_reason="expired",
        )
        expired_count += 1
        logger.info(f"Expired job {job.id} (ttl={job.ttl}s)")

    return expired_count


def claim_next_job_with_queue_priority(
    worker,
    backend,
    queues: list[str],
    queue_priorities: dict[str, int] | None = None,
    tag_concurrency_limits: dict | None = None,
    tag_rate_limits: dict | None = None,
    max_attempts: int = 10,
    enable_registries: bool = True,
):
    """Atomically claim next job with proper queue priority ordering.

    This is the core claiming algorithm, promoted from django_sqlery.
    It loops over candidates, checks concurrency limits, rate limits,
    and dependencies before claiming.

    Args:
        worker: Worker instance that will process the job
        backend: DatabaseBackend instance
        queues: List of queue names to claim from
        queue_priorities: Optional {queue_name: weight} for ordering
        tag_concurrency_limits: Optional {tag: max_concurrent} limits
        tag_rate_limits: Optional {tag: rate_limit_str} limits
        max_attempts: Max candidates to check before giving up
        enable_registries: Whether to track job start in registry

    Returns:
        Job instance if claimed, None if no jobs available
    """
    if tag_concurrency_limits is None:
        tag_concurrency_limits = {}
    if tag_rate_limits is None:
        tag_rate_limits = {}

    # Expire TTL jobs before claiming
    expire_ttl_jobs(backend)

    for attempt in range(max_attempts):
        # Get next claimable job(s)
        candidates = backend.get_claimable_jobs(
            queues=queues,
            priority_weights=queue_priorities,
            limit=1,
        )

        if not candidates:
            return None

        job = candidates[0]

        # Acquire exclusive locks on tag coordination rows
        tags = getattr(job, 'tags', None)
        if tags:
            tags_with_limits = [
                tag for tag in tags
                if tag in tag_rate_limits or tag in tag_concurrency_limits
            ]
            if tags_with_limits:
                sorted_tags = sorted(tags_with_limits)
                backend.acquire_tag_locks(sorted_tags)

        # Check tag concurrency limits
        if not check_tag_concurrency_limits(job, tag_concurrency_limits, backend):
            continue

        # Check tag rate limits
        if not check_tag_rate_limits(job, tag_rate_limits, backend):
            continue

        # Check job dependencies
        if not check_job_dependencies(job):
            continue

        # All checks passed — atomically claim
        if not backend.atomic_claim_job(job, worker):
            continue

        # Successfully claimed! Update worker status
        if hasattr(worker, 'status'):
            worker.status = "busy"
        # Old: if hasattr(worker, 'current_job'): worker.current_job = job
        if hasattr(worker, 'current_job_id'):
            worker.current_job_id = job.id
        if hasattr(worker, 'last_heartbeat'):
            # Refresh heartbeat on claim so an idle worker's stale pre-claim
            # heartbeat doesn't briefly trip the dashboard's stale-worker warning.
            worker.last_heartbeat = datetime.now(timezone.utc)
        if hasattr(worker, 'save'):
            # Old: worker.save(update_fields=["status", "current_job"])
            update_fields = ["status", "current_job_id"]
            if hasattr(worker, 'last_heartbeat'):
                update_fields.append("last_heartbeat")
            worker.save(update_fields=update_fields)

        # Track in registry
        if enable_registries:
            track_job_start(job)

        return job

    # Exhausted max attempts
    return None


def release_job(worker, job, status, **kwargs):
    """Release a job after processing (legacy Django-mode helper).

    Promoted verbatim from django_sqlery/worker_claiming.py so the Django
    worker_process.py runner can keep using it via sqlery.core.claiming.
    Requires Django to be installed; raises RuntimeError if Django is missing.

    Args:
        worker: Worker instance that processed the job
        job: QueuedJob instance to release
        status: Final status ('success' or 'failed')
        **kwargs: Additional fields to update (output, error, traceback, etc.)
    """
    if _django_transaction is None or _django_F is None or _django_timezone is None:
        raise RuntimeError(
            "release_job() requires Django; install django to use this helper or "
            "switch to DatabaseBackend.release_job(job_id) for framework-agnostic code."
        )

    # Lazy import to keep core import-clean when Django is absent.
    from sqlery.django_sqlery.models import QueuedJob
    from sqlery.django_sqlery.settings import get_setting

    with _django_transaction.atomic():
        expected_version = job.version
        finished_at = _django_timezone.now()
        duration_seconds = None
        if job.started_at:
            duration_seconds = (finished_at - job.started_at).total_seconds()

        update_fields = {
            'status': status,
            'worker': None,
            'finished_at': finished_at,
            'duration_seconds': duration_seconds,
            'version': _django_F('version') + 1,
        }

        for key, value in kwargs.items():
            if hasattr(job, key) and key != 'version':
                update_fields[key] = value

        rows_updated = QueuedJob.objects.filter(
            id=job.id,
            version=expected_version,
        ).update(**update_fields)

        if rows_updated == 0:
            logger.warning(
                f"Job {job.id} version conflict in release_job - another process modified it"
            )

        try:
            job.refresh_from_db()
        except QueuedJob.DoesNotExist:
            worker.status = "idle"
            # Old: worker.current_job = None
            worker.current_job_id = None
            worker.jobs_processed += 1
            # Old: worker.save(update_fields=["status", "current_job", "jobs_processed"])
            worker.save(update_fields=["status", "current_job_id", "jobs_processed"])
            return

        if get_setting('ENABLE_REGISTRIES', True):
            track_job_finish(job, status=status)

        worker.status = "idle"
        # Old: worker.current_job = None
        worker.current_job_id = None
        worker.jobs_processed += 1
        # Old: worker.save(update_fields=["status", "current_job", "jobs_processed"])
        worker.save(update_fields=["status", "current_job_id", "jobs_processed"])
