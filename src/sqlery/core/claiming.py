"""Framework-agnostic job claiming algorithm.

Promoted from django_sqlery/worker_claiming.py. The orchestration logic
(loop over candidates, check concurrency, check rate limits, check deps,
claim) is pure algorithm parameterized by backend calls.

All database operations are delegated to the DatabaseBackend ABC.
"""

import logging
import os
import socket

from sqlery.core.utils import parse_rate_limit
from sqlery.core.registry import track_job_start, track_job_finish

logger = logging.getLogger(__name__)


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

    from datetime import datetime, timezone

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
        if hasattr(worker, 'current_job'):
            worker.current_job = job
        if hasattr(worker, 'save'):
            worker.save(update_fields=["status", "current_job"])

        # Track in registry
        if enable_registries:
            track_job_start(job)

        return job

    # Exhausted max attempts
    return None
