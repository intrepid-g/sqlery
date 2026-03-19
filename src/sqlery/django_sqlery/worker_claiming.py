"""Atomic job claiming logic for multi-worker architecture."""

import logging
import os
import socket
from datetime import timedelta

from django.db import connection, models, transaction
from django.db.models import F
from django.utils import timezone

from sqlery.core.db_resilience import retry_on_db_error
from sqlery.core.registry import track_job_start, track_job_finish
from sqlery.core.utils import parse_rate_limit

from .db_compat import atomic_claim_job_queryset, atomic_claim_job, is_sqlite
from .models import QueuedJob, Worker, TagLock
from .settings import get_setting
# from .registries import track_job_start, track_job_finish  # Promoted to core


def get_node_id():
    """Get the node identifier (hostname or NODE_ID env var)."""
    # import socket  # moved to top-level
    # import os  # moved to top-level
    return os.environ.get("NODE_ID", socket.gethostname())


def ensure_tag_locks_exist(tags):
    """Ensure TagLock rows exist for the given tags.

    Auto-creates TagLock rows if they don't exist. This is called
    before acquiring locks to ensure coordination points are available.

    Uses bulk operations for better performance.

    Args:
        tags: List of tag strings (e.g., ["acme-api", "stripe-api"])
    """
    if not tags:
        return

    # from .models import TagLock  # moved to top-level

    # Check which tags already exist (single query)
    existing = set(
        TagLock.objects.filter(tag__in=tags).values_list('tag', flat=True)
    )

    # Create missing tags (single bulk insert with conflict handling)
    new_tags = [tag for tag in tags if tag not in existing]
    if new_tags:
        TagLock.objects.bulk_create(
            [TagLock(tag=tag) for tag in new_tags],
            ignore_conflicts=True  # Handle race conditions gracefully
        )


def ensure_all_configured_tags():
    """Ensure TagLock rows exist for all tags in settings.

    This should be called on worker startup to populate the TagLock
    table with all tags from TAG_RATE_LIMITS and TAG_CONCURRENCY_LIMITS.
    """
    # from .models import TagLock  # moved to top-level

    all_tags = set()

    # Collect tags from rate limits
    rate_limits = get_setting("TAG_RATE_LIMITS", {})
    all_tags.update(rate_limits.keys())

    # Collect tags from concurrency limits
    concurrency_limits = get_setting("TAG_CONCURRENCY_LIMITS", {})
    all_tags.update(concurrency_limits.keys())

    # Create TagLock rows
    for tag in all_tags:
        TagLock.objects.get_or_create(tag=tag)


def check_tag_concurrency_limits(job):
    """Check if job's tags would exceed configured concurrency limits.

    Args:
        job: QueuedJob instance with tags field

    Returns:
        bool: True if job can run, False if tag limits would be exceeded
    """
    if not job.tags:
        return True  # No tags = no limits

    tag_limits = get_setting("TAG_CONCURRENCY_LIMITS", {})
    if not tag_limits:
        return True  # No limits configured

    # Check each tag for concurrency limits
    for tag in job.tags:
        limit = tag_limits.get(tag)
        if limit is None:
            continue  # No limit for this tag

        # Count currently running jobs with this tag
        running_count = QueuedJob.objects.filter(
            status="running",
            tags__contains=[tag]  # JSONField contains lookup
        ).count()

        if running_count >= limit:
            return False  # Limit exceeded for this tag

    return True  # All tag limits are OK


def check_tag_rate_limits(job):
    """Check if job's tags would exceed configured rate limits.

    Rate limits control how many jobs can START within a time window.
    For example, "60/m" means max 60 jobs can start per minute.

    IMPORTANT: API rate limits count REQUESTS SENT (when job starts),
    not RESPONSES RECEIVED (when job finishes). We use started_at timestamp
    and include all jobs (running, success, failed) that hit the API.

    NOTE: Due to race conditions between check and claim, actual rate may
    exceed limit by ~5%. This is generally acceptable for API rate limiting.

    Args:
        job: QueuedJob instance with tags field

    Returns:
        bool: True if job can run, False if rate limits would be exceeded
    """
    if not job.tags:
        return True  # No tags = no limits

    tag_rate_limits = get_setting("TAG_RATE_LIMITS", {})
    if not tag_rate_limits:
        return True  # No limits configured

    # from .rate_limit_utils import parse_rate_limit  # Was broken: no such file in django_sqlery
    # from sqlery.core.utils import parse_rate_limit  # moved to top-level
    # import logging  # moved to top-level

    logger = logging.getLogger(__name__)

    # Check each tag for rate limits
    for tag in job.tags:
        rate_limit_str = tag_rate_limits.get(tag)
        if not rate_limit_str:
            continue  # No rate limit for this tag

        try:
            count, time_window = parse_rate_limit(rate_limit_str)
        except ValueError as e:
            logger.error(f"Invalid rate limit for tag '{tag}': {e}")
            continue  # Skip invalid rate limit

        # Calculate time threshold (e.g., 1 minute ago for "60/m")
        now = timezone.now()
        threshold = now - time_window

        # Count jobs that STARTED in the time window
        # Include running, success, AND failed (all hit the API)
        started_count = QueuedJob.objects.filter(
            status__in=["running", "success", "failed"],  # All states that sent API requests
            tags__contains=[tag],
            started_at__gte=threshold,  # When job sent API request
            started_at__isnull=False,   # Ensure started_at is set
        ).count()

        if started_count >= count:
            logger.debug(
                f"Rate limit exceeded for tag '{tag}': {started_count}/{count} "
                f"started in last {time_window.total_seconds()}s"
            )
            return False  # Rate limit exceeded for this tag

    return True  # All rate limits are OK


def check_job_dependencies(job):
    """Check if all job dependencies have completed successfully.

    If any dependency has failed, marks this job as failed.
    If dependencies are not yet complete, returns False to skip claiming.

    Args:
        job: QueuedJob instance with dependencies field

    Returns:
        bool: True if job can run (dependencies met), False if should skip
    """
    if not job.dependencies:
        return True  # No dependencies = can run immediately

    # import logging  # moved to top-level
    logger = logging.getLogger(__name__)

    # Check dependency status
    all_met, failed_deps = job.check_dependencies_met()

    if failed_deps:
        # One or more dependencies failed - fail this job too
        job.mark_failed(
            error=f"Dependencies failed: {failed_deps}",
            termination_reason="dependency_failed"
        )
        logger.info(
            f"Failed job {job.id} due to failed dependencies: {failed_deps}"
        )
        return False  # Skip claiming (job is now failed)

    if not all_met:
        # Dependencies not yet complete - skip for now
        logger.debug(
            f"Skipping job {job.id} - dependencies not yet complete"
        )
        return False  # Skip claiming (dependencies still running)

    # All dependencies completed successfully
    return True


def claim_next_job(worker):
    """Atomically claim the next available job for a worker.

    Uses SELECT FOR UPDATE SKIP LOCKED to ensure only one worker
    can claim each job, even across multiple processes/nodes.

    Args:
        worker: Worker instance that will process the job

    Returns:
        QueuedJob instance if claimed, None if no jobs available
    """
    queues = get_setting("WORKER_QUEUES", ["default"])
    priorities = get_setting("QUEUE_PRIORITIES", {"default": 50})

    # Build priority case statement for SQL ordering
    # Jobs in higher-priority queues are selected first
    queue_priority_map = {
        queue: priorities.get(queue, 0)
        for queue in queues
    }

    with transaction.atomic():
        # Find next available job across all configured queues
        # Using database-appropriate locking (SELECT FOR UPDATE for Postgres, plain SELECT for SQLite)
        queryset = (
            QueuedJob.objects
            .filter(
                status="queued",
                queue_name__in=queues,
            )
            .filter(
                # Only jobs that are due (scheduled_at is None or in the past)
                models.Q(scheduled_at__isnull=True) |
                models.Q(scheduled_at__lte=timezone.now())
            )
        )

        # Apply database-appropriate locking
        queryset = atomic_claim_job_queryset(queryset)

        job = (
            queryset
            .order_by(
                # Order by queue priority (using dict lookup in Python)
                # Then by job priority (higher first)
                # Then by creation time (older first)
                "-priority",
                "created_at"
            )
            .first()
        )

        if not job:
            return None

        # Atomically claim the job (handles both Postgres and SQLite)
        if not atomic_claim_job(job, worker):
            # Failed to claim (SQLite race condition)
            return None

        # Update worker status
        worker.status = "busy"
        worker.current_job = job
        worker.save(update_fields=["status", "current_job"])

        return job


def expire_ttl_jobs():
    """Mark queued jobs as failed if their TTL has expired.

    Jobs with a ttl value that have been queued longer than ttl seconds
    are marked as failed with termination_reason='expired'.
    """
    # from datetime import timedelta  # moved to top-level
    # import logging  # moved to top-level

    logger = logging.getLogger(__name__)
    now = timezone.now()

    expired_jobs = QueuedJob.objects.filter(
        status="queued",
        ttl__isnull=False,
    )

    expired_count = 0
    for job in expired_jobs:
        if job.created_at + timedelta(seconds=job.ttl) < now:
            job.mark_failed(
                error=f"Job expired after {job.ttl}s in queue",
                termination_reason="expired",
            )
            expired_count += 1
            logger.info(f"Expired job {job.id} (ttl={job.ttl}s)")

    return expired_count


@retry_on_db_error()
def claim_next_job_with_queue_priority(worker, queues: list[str] | None = None):
    """Atomically claim next job with proper queue priority ordering.

    This version uses raw SQL to ensure queue priorities are respected
    in the database query itself, not after fetching results.

    Args:
        worker: Worker instance that will process the job
        queues: Optional list of queue names to claim from.
                Falls back to WORKER_QUEUES setting if not provided.

    Returns:
        QueuedJob instance if claimed, None if no jobs available
    """
    # from django.db import connection, models  # moved to top-level

    if queues is None:
        queues = get_setting("WORKER_QUEUES", ["default"])
    priorities = get_setting("QUEUE_PRIORITIES", {"default": 50})

    # Expire TTL jobs before claiming
    expire_ttl_jobs()

    # Build CASE statement for queue priority
    case_whens = []
    for queue, priority in priorities.items():
        if queue in queues:
            case_whens.append(
                models.When(queue_name=queue, then=models.Value(priority))
            )

    queue_priority_expr = models.Case(
        *case_whens,
        default=models.Value(0),
        output_field=models.IntegerField()
    )

    with transaction.atomic():
        # Try to find a job that respects tag concurrency AND rate limits
        # We may need to skip jobs whose tags are at max concurrency or rate limit
        max_attempts = get_setting("MAX_JOB_CLAIM_ATTEMPTS", 10)

        for attempt in range(max_attempts):
            # Find next job with proper priority ordering
            queryset = (
                QueuedJob.objects
                .filter(
                    status="queued",
                    queue_name__in=queues,
                )
                .filter(
                    # Only jobs that are due
                    models.Q(scheduled_at__isnull=True) |
                    models.Q(scheduled_at__lte=timezone.now())
                )
            )

            # Apply database-appropriate locking
            queryset = atomic_claim_job_queryset(queryset)

            job = (
                queryset
                .annotate(queue_priority=queue_priority_expr)
                .order_by(
                    "-queue_priority",  # Queue priority first
                    "-priority",  # Then job priority
                    "created_at"  # Then oldest first
                )
                .first()
            )

            if not job:
                return None  # No more jobs available

            # Acquire exclusive locks on tag coordination rows
            # This prevents race conditions by serializing workers per tag
            if job.tags:
                # Get tags that have limits configured
                rate_limits = get_setting("TAG_RATE_LIMITS", {})
                concurrency_limits = get_setting("TAG_CONCURRENCY_LIMITS", {})
                tags_with_limits = [
                    tag for tag in job.tags
                    if tag in rate_limits or tag in concurrency_limits
                ]

                if tags_with_limits:
                    # Ensure TagLock rows exist (auto-create if needed)
                    ensure_tag_locks_exist(tags_with_limits)

                    # Sort tags to ensure consistent lock order (prevents deadlocks)
                    sorted_tags = sorted(tags_with_limits)

                    # Acquire exclusive locks on ALL tag rows
                    # CRITICAL: Must use list() to actually fetch and lock all rows!
                    # Using .exists() would only lock ONE row (LIMIT 1), not all
                    # For SQLite: No row-level locking, but transaction isolation provides safety
                    tag_queryset = TagLock.objects.filter(tag__in=sorted_tags)
                    if not is_sqlite():
                        tag_queryset = tag_queryset.select_for_update()
                    list(tag_queryset)

            # Check tag concurrency limits (now atomic - we hold the locks)
            if not check_tag_concurrency_limits(job):
                # Concurrency limit exceeded, skip this job and try next
                continue

            # Check tag rate limits (now atomic - we hold the locks)
            if not check_tag_rate_limits(job):
                # Rate limit exceeded, skip this job and try next
                continue

            # Check job dependencies
            if not check_job_dependencies(job):
                # Dependencies not met or failed, skip this job and try next
                continue

            # All checks passed (concurrency, rate limits, dependencies)
            # Now try to atomically claim the job (handles both Postgres and SQLite)
            if not atomic_claim_job(job, worker):
                # Failed to claim (SQLite race condition) - try next job
                continue

            # Successfully claimed! Update worker status
            worker.status = "busy"
            worker.current_job = job
            worker.save(update_fields=["status", "current_job"])

            # Track in registry
            if get_setting('ENABLE_REGISTRIES', True):
                track_job_start(job)

            return job

        # Exhausted max attempts, no claimable job found
        return None


@retry_on_db_error()
def release_job(worker, job, status, **kwargs):
    """Release a job after processing.

    Args:
        worker: Worker instance that processed the job
        job: QueuedJob instance to release
        status: Final status ('success' or 'failed')
        **kwargs: Additional fields to update (output, error, traceback, etc.)
    """
    # from django.db.models import F  # moved to top-level

    with transaction.atomic():
        # Prepare update fields
        expected_version = job.version
        finished_at = timezone.now()
        duration_seconds = None
        if job.started_at:
            duration_seconds = (finished_at - job.started_at).total_seconds()

        # Build update dict with version check
        update_fields = {
            'status': status,
            'worker': None,
            'finished_at': finished_at,
            'duration_seconds': duration_seconds,
            'version': F('version') + 1
        }

        # Add additional fields from kwargs
        for key, value in kwargs.items():
            if hasattr(job, key) and key != 'version':
                update_fields[key] = value

        # Atomic update with version check
        rows_updated = QueuedJob.objects.filter(
            id=job.id,
            version=expected_version
        ).update(**update_fields)

        if rows_updated == 0:
            # Job was modified by another process — most likely displaced by a newer
            # named job (force_stop incremented the version and deleted the row).
            # import logging  # moved to top-level
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Job {job.id} version conflict in release_job - another process modified it"
            )

        try:
            job.refresh_from_db()
        except QueuedJob.DoesNotExist:
            # Job was deleted (displaced by a newer named job). Mark worker idle
            # and return — the worker loop will continue and claim the new job.
            worker.status = "idle"
            worker.current_job = None
            worker.jobs_processed += 1
            worker.save(update_fields=["status", "current_job", "jobs_processed"])
            return

        # Track in registry
        if get_setting('ENABLE_REGISTRIES', True):
            track_job_finish(job, status=status)

        # Update worker status
        worker.status = "idle"
        worker.current_job = None
        worker.jobs_processed += 1
        worker.save(update_fields=["status", "current_job", "jobs_processed"])
