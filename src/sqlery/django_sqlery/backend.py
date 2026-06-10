"""Django ORM backend implementation for sqlery.

This backend wraps Django ORM operations to implement the DatabaseBackend interface.
"""

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from django.db import connection, IntegrityError, models as db_models, transaction
from django.db.models import Q, Count
from django.utils import timezone

from sqlery.core.db_resilience import retry_on_db_error

from ..compat import DatabaseBackend
from .db_compat import atomic_claim_job, atomic_claim_job_queryset, is_sqlite
from .models import DaemonLease, QueuedJob, ScheduledTask, JobRegistry, TagLock, Worker
from sqlery.core.claiming import claim_next_job_with_queue_priority

logger = logging.getLogger(__name__)

CLEANUP_BATCH_SIZE = 500
FINISHED_STATUSES = ("success", "failed", "archived")


class DjangoBackend(DatabaseBackend):
    """Django ORM implementation of DatabaseBackend.

    Uses Django models and QuerySets for all database operations.
    """

    def __init__(self):
        """Initialize Django backend."""
        # Import models here to avoid circular imports
        # from .models import QueuedJob, ScheduledTask, JobRegistry, Worker  # moved to top-level

        self.QueuedJob = QueuedJob
        self.ScheduledTask = ScheduledTask
        self.JobRegistry = JobRegistry
        self.Worker = Worker

    def create_job(
        self,
        task_path: str,
        kwargs: dict,
        queue_name: str,
        priority: int,
        scheduled_at: datetime | None,
        max_retries: int,
        retry_backoff: float,
        allow_parallel: bool,
        timeout_seconds: int | None,
        retry_count: int | None = None,
        scheduled_task_id: int | None = None,
        job_name: str | None = None,
        retry_intervals: list | None = None,
        meta: dict | None = None,
        dependencies: list | None = None,
        on_success_path: str = "",
        on_failure_path: str = "",
        ttl: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        parent_job_id: int | None = None,
    ):
        """Create a new job in the database."""
        # If a named job is requested, the new job always wins.  Stop any running
        # conflict (SIGTERM + mark failed) before deleting all records with that name.
        if job_name:
            for conflicting in self.QueuedJob.objects.filter(job_name=job_name):
                if conflicting.status == "running":
                    conflicting.force_stop()
            self.QueuedJob.objects.filter(job_name=job_name).delete()
        job = self.QueuedJob.objects.create(
            task_path=task_path,
            kwargs=kwargs,
            queue_name=queue_name,
            priority=priority,
            scheduled_at=scheduled_at,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count if retry_count is not None else 0,
            scheduled_task_id=scheduled_task_id,
            job_name=job_name,
            retry_intervals=retry_intervals,
            meta=meta,
            dependencies=dependencies or [],
            on_success_path=on_success_path,
            on_failure_path=on_failure_path,
            ttl=ttl,
            result_ttl=result_ttl,
            failure_ttl=failure_ttl,
            parent_job_id=parent_job_id,
            status="queued",
        )
        return job

    @retry_on_db_error()
    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim next available job using the unified claiming path.

        Delegates to claim_next_job_with_queue_priority which enforces tag
        concurrency, rate limits, dependency checks, TTL expiry, and
        optimistic locking.
        """
        # # Old claim_job body — bypassed tag concurrency, rate limits,
        # # dependency checks, TTL expiry, and optimistic locking.
        # from django.db import connection
        #
        # with transaction.atomic():
        #     query = atomic_claim_job_queryset(
        #         self.QueuedJob.objects.filter(
        #             queue_name__in=queues,
        #             status="queued",
        #         ).filter(
        #             Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
        #         )
        #     ).order_by('-priority', 'created_at')
        #
        #     job = query.first()
        #
        #     if job:
        #         worker_row = self._resolve_worker(worker_id)
        #
        #         if worker_row:
        #             abandoned = self.QueuedJob.objects.filter(
        #                 worker=worker_row, status='running'
        #             ).exclude(id=job.id)
        #             for orphan in abandoned:
        #                 orphan.status = 'failed'
        #                 orphan.finished_at = timezone.now()
        #                 orphan.error = 'Worker claimed a new job — this job was abandoned'
        #                 orphan.save(update_fields=['status', 'finished_at', 'error'])
        #                 logger.info(f"Failed abandoned job #{orphan.id} (worker claimed #{job.id})")
        #
        #         job.status = "running"
        #         job.started_at = timezone.now()
        #         job.worker_pid = os.getpid()
        #         update_fields = ['status', 'started_at', 'worker_pid']
        #         if worker_row:
        #             job.worker = worker_row
        #             update_fields.append('worker')
        #         job.save(update_fields=update_fields)
        #
        #         if worker_row:
        #             worker_row.status = 'busy'
        #             worker_row.current_job = job
        #             worker_row.last_heartbeat = timezone.now()
        #             worker_row.save(update_fields=['status', 'current_job', 'last_heartbeat'])
        #
        #     return job

        # REGRESSION 2026-05-18: select_for_update used outside transaction
        # Root cause: claim logic moved to framework-agnostic module without Django transaction wrapper
        # Fix: restore transaction.atomic() that existed in the old claim_job body
        worker_row = self._resolve_worker(worker_id)
        if not worker_row:
            # I wish I had the time to: add a retry loop with exponential backoff
            # before auto-registering, to handle transient visibility delays
            worker_row = self._auto_register_worker(worker_id)
            if not worker_row:
                return None
        with transaction.atomic():
            return claim_next_job_with_queue_priority(worker_row, self, queues=queues)

    @retry_on_db_error()
    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        """Release a job after processing and update worker state."""
        with transaction.atomic():
            job.status = status
            job.finished_at = timezone.now()
            if job.started_at:
                job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
            # job.worker = None  # Keep worker FK for historical tracking
            update_fields = ["status", "finished_at", "duration_seconds"]
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
                    update_fields.append(key)
            job.save(update_fields=update_fields)

            # Update worker back to idle
            worker_row = self._resolve_worker(worker_id)
            if worker_row:
                worker_row.status = "idle"
                worker_row.current_job = None
                worker_row.jobs_processed = jobs_processed
                worker_row.last_heartbeat = timezone.now()
                worker_row.save(
                    update_fields=["status", "current_job", "jobs_processed", "last_heartbeat"]
                )

        return job

    def _resolve_worker(self, worker_id: str):
        """Resolve a worker_id string to a Worker model instance."""
        # import uuid  # moved to top-level

        # Try UUID first
        try:
            worker_uuid = uuid.UUID(worker_id)
            return self.Worker.objects.filter(id=worker_uuid).first()
        except ValueError:
            pass

        # Parse "worker_<node>_<pid>" format
        parts = worker_id.split("_")
        if parts[0] == "worker" and len(parts) >= 3:
            pid = int(parts[-1])
            node_id = "_".join(parts[1:-1])
            return self.Worker.objects.filter(node_id=node_id, pid=pid).first()

        return None

    def _auto_register_worker(self, worker_id: str):
        """Auto-register a worker on-demand if not found in database.

        This prevents a race condition where a worker's heartbeat hasn't
        propagated yet when it tries to claim its first job.

        Args:
            worker_id: Worker identifier string (e.g., "worker_node_12345")

        Returns:
            Worker instance if created, None if worker_id format is invalid
        """
        # I wish I had the time to: validate worker_id against a whitelist
        # or require a shared secret to prevent unauthorized worker registration

        parts = worker_id.split("_")
        if parts[0] != "worker" or len(parts) < 3:
            return None

        pid = int(parts[-1])
        node_id = "_".join(parts[1:-1])

        worker, created = self.Worker.objects.get_or_create(
            node_id=node_id,
            pid=pid,
            defaults={
                "status": "idle",
                "last_heartbeat": timezone.now(),
            },
        )

        if created:
            logger.info(f"Auto-registered worker {worker_id} on-demand")

        return worker

    def is_worker_paused(self, worker_id: str) -> bool:
        """Check if worker is currently paused."""
        worker = self._resolve_worker(worker_id)
        if not worker or not worker.paused_until:
            return False
        if worker.paused_until > timezone.now():
            return True
        # Pause expired, clear it
        worker.paused_until = None
        worker.save(update_fields=["paused_until"])
        return False

    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """Get queue statistics (counts by status)."""
        query = self.QueuedJob.objects.all()

        if queue_name:
            query = query.filter(queue_name=queue_name)

        stats = query.values("status").annotate(count=Count("id"))

        result = {
            "queued": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
        }

        for stat in stats:
            result[stat["status"]] = stat["count"]

        if queue_name:
            result["queue_name"] = queue_name

        return result

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued job."""
        updated = self.QueuedJob.objects.filter(id=job_id, status="queued").update(
            status="failed", error="Cancelled by user"
        )

        return updated > 0

    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        """Retry failed jobs by resetting them to queued status."""
        query = self.QueuedJob.objects.filter(status="failed")

        if queue_name:
            query = query.filter(queue_name=queue_name)

        if max_jobs:
            job_ids = list(query.values_list("id", flat=True)[:max_jobs])
            query = self.QueuedJob.objects.filter(id__in=job_ids)

        count = query.update(
            status="queued",
            error="",
            traceback="",
            retry_count=0,
        )

        return count

    def get_due_scheduled_tasks(self):
        """Get scheduled tasks that are due to run."""
        return list(
            self.ScheduledTask.objects.filter(
                enabled=True, next_run_at__lte=timezone.now()
            ).order_by("next_run_at")
        )

    def create_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str,
        priority: int,
        enabled: bool = True,
    ):
        """Create a new scheduled task."""
        task = self.ScheduledTask.objects.create(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
        )
        return task

    def get_worker_heartbeats(self, active_only: bool = True):
        """Get worker heartbeats."""
        query = self.Worker.objects.all()

        if active_only:
            threshold = timezone.now() - timedelta(seconds=60)
            query = query.filter(last_heartbeat__gte=threshold).exclude(status="dead")

        return list(query.order_by("-last_heartbeat"))

    @retry_on_db_error()
    def update_worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
        jobs_processed: int | None = None,
        total_busy_seconds: float | None = None,
    ):
        """Update or create worker heartbeat."""
        # import socket  # moved to top-level
        # import uuid  # moved to top-level

        # Check if worker_id is a UUID (from cleanup_dead_workers)
        try:
            worker_uuid = uuid.UUID(worker_id)
            # Update by UUID - DO NOT update heartbeat when marking as dead
            update_fields = {
                "status": status,
                "current_job_id": current_job_id,
            }
            # Only update heartbeat for active workers, not dead ones
            if status != "dead":
                update_fields["last_heartbeat"] = timezone.now()
            # Update jobs_processed if provided
            if jobs_processed is not None:
                update_fields["jobs_processed"] = jobs_processed
            if total_busy_seconds is not None:
                update_fields["total_busy_seconds"] = total_busy_seconds

            self.Worker.objects.filter(id=worker_uuid).update(**update_fields)
            return
        except ValueError:
            # Not a UUID, parse as worker_id format
            pass

        # Extract node_id and pid from worker_id parameter
        # Format: "worker_<node>_<pid>" or "daemon_<node>"
        parts = worker_id.split("_")

        if parts[0] == "daemon":
            # Daemon heartbeat - use PID 0 as special marker for daemon processes
            node_id = "_".join(parts[1:])  # Join remaining parts for node_id
            pid = 0
        elif parts[0] == "worker":
            # Worker heartbeat - extract node and pid from worker_id
            # Format: worker_<node>_<pid>
            pid = int(parts[-1])  # Last part is PID
            node_id = "_".join(parts[1:-1])  # Middle parts are node_id
        else:
            # Fallback for unknown format - use current process
            node_id = socket.gethostname()
            pid = os.getpid()

        defaults = {
            "status": status,
            "current_job_id": current_job_id,
            "last_heartbeat": timezone.now(),
        }
        # Update jobs_processed if provided
        if jobs_processed is not None:
            defaults["jobs_processed"] = jobs_processed
        if total_busy_seconds is not None:
            defaults["total_busy_seconds"] = total_busy_seconds

        # Try lightweight UPDATE first (no row lock contention).
        # Only fall back to update_or_create for initial registration.
        rows = self.Worker.objects.filter(node_id=node_id, pid=pid).update(**defaults)
        if rows == 0:
            self.Worker.objects.update_or_create(node_id=node_id, pid=pid, defaults=defaults)

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete any Worker row for this worker_id (stale record from a crashed run).

        Called once at worker startup so the subsequent update_or_create creates
        a fresh row with a reset started_at instead of reusing the old one.
        """
        worker_row = self._resolve_worker(worker_id)
        if worker_row:
            worker_row.delete()
            return 1
        return 0

    def refresh_worker_heartbeat(self, worker_id):
        """Update ONLY last_heartbeat for a worker. Does not touch status or current_job.

        Used by the daemon to keep workers alive without interfering with
        the worker's own state management (status, current_job).
        """
        # import uuid  # moved to top-level

        try:
            worker_uuid = uuid.UUID(str(worker_id))
            self.Worker.objects.filter(id=worker_uuid).update(last_heartbeat=timezone.now())
        except (ValueError, TypeError):
            logger.warning(f"refresh_worker_heartbeat: invalid worker_id {worker_id}")

    def cleanup_jobs(
        self,
        status: str | None = None,
        max_age_days: int | None = None,
        max_count: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up old jobs based on retention policy."""
        query = self.QueuedJob.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        if max_age_days:
            cutoff = timezone.now() - timedelta(days=max_age_days)
            query = query.filter(created_at__lt=cutoff)

        if dry_run:
            count = query.count()
            return {"deleted": 0, "count": count}

        # Old: unbounded delete that holds table lock for the full result set
        # count = query.count()
        # if not dry_run:
        #     query.delete()
        # return {"deleted": count, "count": count}

        # Keyset-batched loop: at most CLEANUP_BATCH_SIZE rows per DELETE,
        # status re-check prevents deleting a row claimed mid-loop.
        total_deleted = 0
        while True:
            ids = list(query.order_by("id").values_list("id", flat=True)[:CLEANUP_BATCH_SIZE])
            if not ids:
                break
            deleted_count, _ = self.QueuedJob.objects.filter(
                id__in=ids, status__in=FINISHED_STATUSES
            ).delete()
            total_deleted += deleted_count
            time.sleep(0.1)

        return {"deleted": total_deleted, "count": total_deleted}

    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up jobs by keeping only the most recent N jobs."""
        query = self.QueuedJob.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        # Get IDs of jobs to keep (most recent N)
        keep_ids = list(query.order_by("-created_at").values_list("id", flat=True)[:keep_count])

        # Delete jobs not in keep list
        delete_query = query.exclude(id__in=keep_ids)
        count = delete_query.count()
        if not dry_run:
            delete_query.delete()

        return {"deleted": count, "count": count, "kept": len(keep_ids)}

    def get_database_stats(self) -> dict:
        """Get database statistics."""
        job_counts = self.QueuedJob.objects.values("status").annotate(count=Count("id"))
        registry_counts = self.JobRegistry.objects.values("registry_type").annotate(
            count=Count("id")
        )

        stats = {
            "total_jobs": self.QueuedJob.objects.count(),
            "job_counts": {item["status"]: item["count"] for item in job_counts},
            "total_registries": self.JobRegistry.objects.count(),
            "registry_counts": {item["registry_type"]: item["count"] for item in registry_counts},
            "total_scheduled_tasks": self.ScheduledTask.objects.count(),
            "enabled_scheduled_tasks": self.ScheduledTask.objects.filter(enabled=True).count(),
            "total_workers": self.Worker.objects.count(),
        }

        return stats

    @retry_on_db_error()
    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL VACUUM)."""
        # from django.db import connection  # moved to top-level

        with connection.cursor() as cursor:
            try:
                if is_sqlite():
                    # SQLite: single VACUUM for entire database (no per-table or ANALYZE)
                    cursor.execute("VACUUM")
                else:
                    # PostgreSQL: per-table VACUUM ANALYZE
                    cursor.execute("VACUUM ANALYZE sqlery_queued_job")
                    cursor.execute("VACUUM ANALYZE sqlery_scheduled_task")
                    cursor.execute("VACUUM ANALYZE sqlery_registry")
                    cursor.execute("VACUUM ANALYZE sqlery_worker")
                return {"success": True, "message": "Database vacuumed successfully"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def add_job_to_registry(
        self,
        job_id: int,
        registry_type: str,
        metadata: dict | None = None,
    ):
        """Add job to a registry for lifecycle tracking."""
        self.JobRegistry.objects.create(
            job_id=job_id,
            registry_type=registry_type,
            metadata=metadata or {},
        )

    def remove_job_from_registry(self, job_id: int, registry_type: str):
        """Remove job from a registry."""
        self.JobRegistry.objects.filter(
            job_id=job_id,
            registry_type=registry_type,
            exited_at__isnull=True,
        ).update(exited_at=timezone.now())

    def get_registry_jobs(
        self,
        registry_type: str,
        queue_name: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Get jobs in a specific registry."""
        query = self.JobRegistry.objects.filter(
            registry_type=registry_type,
            exited_at__isnull=True,
        ).select_related("job")

        if queue_name:
            query = query.filter(job__queue_name=queue_name)

        if limit:
            query = query[:limit]

        return [entry.job for entry in query]

    def cleanup_registry(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
    ) -> dict:
        """Clean up old registry entries."""
        query = self.JobRegistry.objects.all()

        if registry_type:
            query = query.filter(registry_type=registry_type)

        if max_age_days:
            cutoff = timezone.now() - timedelta(days=max_age_days)
            query = query.filter(entered_at__lt=cutoff)

        count = query.count()
        query.delete()

        return {"deleted": count}

    def get_job_by_id(self, job_id: int):
        """Get job by ID."""
        try:
            return self.QueuedJob.objects.get(id=job_id)
        except self.QueuedJob.DoesNotExist:
            return None

    def mark_job_success(self, job_id: int, output: str = ""):
        """Mark job as successful."""
        job = self.get_job_by_id(job_id)
        if job:
            job.mark_success(output=output)
        return job

    def mark_job_failed(self, job_id: int, error: str, traceback: str = ""):
        """Mark job as failed."""
        job = self.get_job_by_id(job_id)
        if job:
            job.mark_failed(error=error, traceback=traceback)
        return job

    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it)."""
        self.QueuedJob.objects.filter(id=job_id, status="failed").update(status="archived")

    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk parent_job_id chain, set all ancestors to given status."""
        current_id = (
            self.QueuedJob.objects.filter(id=job_id).values_list("parent_job_id", flat=True).first()
        )
        while current_id:
            self.QueuedJob.objects.filter(id=current_id).update(status=status)
            current_id = (
                self.QueuedJob.objects.filter(id=current_id)
                .values_list("parent_job_id", flat=True)
                .first()
            )

    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        """Check if scheduled task has pending jobs."""
        return self.QueuedJob.objects.filter(
            scheduled_task_id=task_id,
            status__in=["queued", "running"],
        ).exists()

    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        """Update scheduled task's next run time."""
        self.ScheduledTask.objects.filter(id=task_id).update(next_run_at=next_run_at)

    def update_scheduled_task(self, task_id: int, **updates) -> Any:
        """Update scheduled task fields."""
        self.ScheduledTask.objects.filter(id=task_id).update(**updates)
        return self.get_scheduled_task(task_id)

    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete scheduled task."""
        deleted, _ = self.ScheduledTask.objects.filter(id=task_id).delete()
        return deleted > 0

    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        """Get all scheduled tasks."""
        query = self.ScheduledTask.objects.all()

        if enabled_only:
            query = query.filter(enabled=True)

        return list(query.order_by("name"))

    def get_scheduled_task(self, task_id: int):
        """Get scheduled task by ID."""
        try:
            return self.ScheduledTask.objects.get(id=task_id)
        except self.ScheduledTask.DoesNotExist:
            return None

    def get_running_jobs(self, queue_name: str | None = None) -> list:
        """Get currently running jobs."""
        query = self.QueuedJob.objects.filter(status="running")

        if queue_name:
            query = query.filter(queue_name=queue_name)

        return list(query)

    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        """Build RunningJobLiveness records for the zombie sweep.

        Preserves the original daemon query: select_related('worker') over all
        ``status='running'`` jobs, optionally filtered to ``queue_names``.
        """
        from sqlery.core.liveness import RunningJobLiveness

        query = self.QueuedJob.objects.filter(status="running")
        if queue_names:
            query = query.filter(queue_name__in=queue_names)
        query = query.select_related("worker")

        records = []
        for job in query:
            worker = job.worker
            records.append(
                RunningJobLiveness(
                    job_id=job.id,
                    started_at=job.started_at,
                    worker_pid=job.worker_pid,
                    worker_node_id=worker.node_id if worker else None,
                    worker_status=worker.status if worker else None,
                    worker_current_job_id=worker.current_job_id if worker else None,
                    worker_last_heartbeat=worker.last_heartbeat if worker else None,
                    worker_friendly_name=worker.friendly_name if worker else None,
                    has_worker=worker is not None,
                )
            )
        return records

    def fail_zombie_job(self, job_id: int, reason: str) -> bool:
        """Mark a running job failed with termination_reason='zombie_job'."""
        job = self.QueuedJob.objects.filter(id=job_id).first()
        if job is None:
            return False
        job.mark_failed(error=reason, termination_reason="zombie_job")
        return True

    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        """Check if queue has running jobs."""
        query = self.QueuedJob.objects.filter(
            queue_name=queue_name,
            status="running",
        )

        if exclude_job_id:
            query = query.exclude(id=exclude_job_id)

        return query.exists()

    def update_job_child_pid(self, job_id: int, child_pid: int):
        """Store the forked child PID on the job row."""
        self.QueuedJob.objects.filter(id=job_id).update(child_pid=child_pid)

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs with the given tag."""
        return self.QueuedJob.objects.filter(
            status="running",
            tags__contains=[tag],
        ).count()

    def count_started_with_tag_since(self, tag: str, threshold) -> int:
        """Count jobs with the given tag that started since threshold."""
        return self.QueuedJob.objects.filter(
            status__in=["running", "success", "failed"],
            tags__contains=[tag],
            started_at__gte=threshold,
            started_at__isnull=False,
        ).count()

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired."""
        # from datetime import timedelta  # moved to top-level

        now = timezone.now()
        expired = []
        for job in self.QueuedJob.objects.filter(status="queued", ttl__isnull=False):
            if job.created_at + timedelta(seconds=job.ttl) < now:
                expired.append(job)
        return expired

    def acquire_tag_locks(self, tags: list[str]) -> None:
        """Acquire exclusive locks on tag coordination rows."""
        # from .models import TagLock  # moved to top-level
        # from .db_compat import is_sqlite  # moved to top-level

        # Ensure TagLock rows exist
        existing = set(TagLock.objects.filter(tag__in=tags).values_list("tag", flat=True))
        new_tags = [tag for tag in tags if tag not in existing]
        if new_tags:
            TagLock.objects.bulk_create(
                [TagLock(tag=tag) for tag in new_tags],
                ignore_conflicts=True,
            )

        # Acquire exclusive locks (Postgres: SELECT FOR UPDATE, SQLite: no-op)
        tag_queryset = TagLock.objects.filter(tag__in=tags)
        if not is_sqlite():
            tag_queryset = tag_queryset.select_for_update()
        list(tag_queryset)  # Force evaluation to actually acquire locks

    def get_claimable_jobs(
        self,
        queues: list[str],
        priority_weights: dict[str, int] | None = None,
        limit: int = 1,
    ) -> list:
        """Get next claimable jobs ordered by queue priority, job priority, age."""
        # from django.db import models as db_models  # moved to top-level

        queryset = self.QueuedJob.objects.filter(status="queued", queue_name__in=queues).filter(
            db_models.Q(scheduled_at__isnull=True) | db_models.Q(scheduled_at__lte=timezone.now())
        )

        queryset = atomic_claim_job_queryset(queryset)

        # Build CASE expression for queue priority ordering
        if priority_weights:
            case_whens = [
                db_models.When(queue_name=queue, then=db_models.Value(weight))
                for queue, weight in priority_weights.items()
                if queue in queues
            ]
            if case_whens:
                queue_priority_expr = db_models.Case(
                    *case_whens,
                    default=db_models.Value(0),
                    output_field=db_models.IntegerField(),
                )
                queryset = queryset.annotate(queue_priority=queue_priority_expr)
                queryset = queryset.order_by("-queue_priority", "-priority", "created_at")
            else:
                queryset = queryset.order_by("-priority", "created_at")
        else:
            queryset = queryset.order_by("-priority", "created_at")

        return list(queryset[:limit])

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker."""
        # from .db_compat import atomic_claim_job  # moved to top-level
        return atomic_claim_job(job, worker)

    def claim_due_scheduled_task(self, task_id: int):
        """Atomically claim a scheduled task for processing."""
        try:
            return atomic_claim_job_queryset(
                self.ScheduledTask.objects.filter(
                    id=task_id,
                    enabled=True,
                    next_run_at__lte=timezone.now(),
                )
            ).get()
        except self.ScheduledTask.DoesNotExist:
            return None

    def release_job(self, job_id: int):
        """Release a claimed job back to queued status."""
        self.QueuedJob.objects.filter(id=job_id).update(
            status="queued",
            started_at=None,
            worker_pid=None,
        )

    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get jobs with optional filtering and pagination."""
        query = self.QueuedJob.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        # Order by priority (desc) and created_at (asc)
        query = query.order_by("-priority", "created_at")

        # Apply pagination
        return list(query[offset : offset + limit])

    def count_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
    ) -> int:
        """Count jobs with optional filtering."""
        query = self.QueuedJob.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        return query.count()

    def claim_queue_leases(
        self,
        queues: list[str],
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> list[str]:
        """Claim scheduler leases for the given queues.

        Returns the subset of queues successfully claimed. Expired leases are
        taken over atomically; live leases held by other daemons are skipped.
        """
        # from .models import DaemonLease  # moved to top-level
        # from django.db import IntegrityError  # moved to top-level

        claimed = []
        for queue_name in queues:
            if self._claim_one_lease(queue_name, daemon_id, node_id, pid, lease_secs):
                claimed.append(queue_name)
        return claimed

    @transaction.atomic
    def _claim_one_lease(
        self,
        queue_name: str,
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> bool:
        """Atomically claim a single queue lease. Returns True if claimed."""
        # from .models import DaemonLease  # moved to top-level
        # from django.db import IntegrityError  # moved to top-level
        # from datetime import timedelta  # moved to top-level

        now = timezone.now()
        expires = now + timedelta(seconds=lease_secs)

        # Take over any expired lease
        updated = DaemonLease.objects.filter(
            queue_name=queue_name,
            expires_at__lt=now,
        ).update(
            daemon_id=daemon_id,
            node_id=node_id,
            pid=pid,
            acquired_at=now,
            expires_at=expires,
        )
        if updated:
            return True

        # Fresh insert (no existing row)
        try:
            DaemonLease.objects.create(
                queue_name=queue_name,
                daemon_id=daemon_id,
                node_id=node_id,
                pid=pid,
                acquired_at=now,
                expires_at=expires,
            )
            return True
        except IntegrityError:
            return False  # Live lease held by another daemon

    def renew_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
        lease_secs: int,
    ) -> None:
        """Extend expires_at for all owned leases by lease_secs from now."""
        # from .models import DaemonLease  # moved to top-level
        # from datetime import timedelta  # moved to top-level

        DaemonLease.objects.filter(
            queue_name__in=owned_queues,
            daemon_id=daemon_id,
        ).update(expires_at=timezone.now() + timedelta(seconds=lease_secs))

    def release_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
    ) -> None:
        """Delete lease rows for all owned queues on clean shutdown."""
        # from .models import DaemonLease  # moved to top-level

        DaemonLease.objects.filter(
            queue_name__in=owned_queues,
            daemon_id=daemon_id,
        ).delete()
