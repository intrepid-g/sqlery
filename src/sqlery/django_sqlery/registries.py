"""RQ-compatible registry system for job lifecycle tracking."""

from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from .models import JobRegistry, QueuedJob
from .settings import get_setting


class RegistryManager:
    """Manage job registries (RQ-compatible API)."""

    def __init__(self, queue_name="default"):
        """Initialize registry manager for a queue.

        Args:
            queue_name: Queue name to filter jobs by
        """
        self.queue_name = queue_name

    def add_to_registry(self, job, registry_type, metadata=None):
        """Add job to a registry.

        Args:
            job: QueuedJob instance
            registry_type: Registry type (started/finished/failed/etc)
            metadata: Optional metadata dict
        """
        JobRegistry.objects.create(
            job=job,
            registry_type=registry_type,
            metadata=metadata or {}
        )

    def remove_from_registry(self, job, registry_type):
        """Remove job from registry (mark as exited).

        Args:
            job: QueuedJob instance
            registry_type: Registry type to remove from
        """
        JobRegistry.objects.filter(
            job=job,
            registry_type=registry_type,
            exited_at__isnull=True
        ).update(exited_at=timezone.now())

    def get_registry(self, registry_type):
        """Get all active jobs in a registry.

        Args:
            registry_type: Registry type

        Returns:
            QuerySet of active JobRegistry entries
        """
        return (
            JobRegistry.objects
            .filter(
                registry_type=registry_type,
                exited_at__isnull=True,
                job__queue_name=self.queue_name
            )
            .select_related('job')
        )

    # RQ-compatible methods

    def get_started_jobs(self):
        """Get all currently running jobs.

        Returns:
            QuerySet of started jobs
        """
        return self.get_registry('started')

    def get_finished_jobs(self, limit=None):
        """Get completed jobs.

        Args:
            limit: Optional limit on number of jobs returned

        Returns:
            QuerySet of finished jobs
        """
        qs = self.get_registry('finished')
        return qs[:limit] if limit else qs

    def get_failed_jobs(self, limit=None):
        """Get failed jobs.

        Args:
            limit: Optional limit on number of jobs returned

        Returns:
            QuerySet of failed jobs
        """
        qs = self.get_registry('failed')
        return qs[:limit] if limit else qs

    def get_scheduled_jobs(self):
        """Get scheduled jobs.

        Returns:
            QuerySet of scheduled jobs
        """
        return self.get_registry('scheduled')

    def get_deferred_jobs(self):
        """Get deferred jobs (waiting for dependencies).

        Returns:
            QuerySet of deferred jobs
        """
        return self.get_registry('deferred')

    def get_canceled_jobs(self):
        """Get canceled jobs.

        Returns:
            QuerySet of canceled jobs
        """
        return self.get_registry('canceled')

    def cleanup_registry(self, registry_type, max_age_days=None):
        """Remove old entries from registry.

        Args:
            registry_type: Registry type to clean
            max_age_days: Max age in days (default: from settings)

        Returns:
            Number of entries deleted
        """
        if max_age_days is None:
            retention = get_setting('REGISTRY_RETENTION', {})
            max_age_days = retention.get(registry_type, 7)

        cutoff = timezone.now() - timedelta(days=max_age_days)

        deleted_count, _ = JobRegistry.objects.filter(
            registry_type=registry_type,
            entered_at__lt=cutoff
        ).delete()

        return deleted_count

    def count(self, registry_type):
        """Count active entries in a registry.

        Args:
            registry_type: Registry type

        Returns:
            int: Number of active entries
        """
        return self.get_registry(registry_type).count()


def get_registry(registry_type, queue='default'):
    """Get a registry for a specific queue (RQ-compatible helper).

    Args:
        registry_type: Registry type (started/finished/failed/etc)
        queue: Queue name

    Returns:
        RegistryManager instance configured for the registry type
    """
    manager = RegistryManager(queue_name=queue)
    # Return a wrapper that pre-filters by registry type
    class RegistryWrapper:
        def __init__(self, manager, registry_type):
            self.manager = manager
            self.registry_type = registry_type

        def get_jobs(self):
            return self.manager.get_registry(self.registry_type)

        def count(self):
            return self.manager.count(self.registry_type)

        def cleanup(self, max_age_days=None):
            return self.manager.cleanup_registry(self.registry_type, max_age_days)

    return RegistryWrapper(manager, registry_type)


def track_job_start(job):
    """Track job starting (add to started registry).

    Args:
        job: QueuedJob instance
    """
    manager = RegistryManager(job.queue_name)
    manager.add_to_registry(job, 'started', {
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'worker_id': str(job.worker.id) if job.worker else None,
    })


def track_job_finish(job, status='success'):
    """Track job completion (move from started to finished/failed).

    Args:
        job: QueuedJob instance
        status: 'success' or 'failed'
    """
    manager = RegistryManager(job.queue_name)

    # Remove from started registry
    manager.remove_from_registry(job, 'started')

    # Add to appropriate completion registry
    if status == 'success':
        manager.add_to_registry(job, 'finished', {
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'duration': job.duration_seconds,
            'output': job.output[:1000] if job.output else '',  # Truncate
        })
    elif status == 'failed':
        manager.add_to_registry(job, 'failed', {
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'duration': job.duration_seconds,
            'error': job.error[:1000] if job.error else '',  # Truncate
            'traceback': job.traceback[:2000] if job.traceback else '',  # Truncate
        })


def track_job_schedule(job):
    """Track job being scheduled for later.

    Args:
        job: QueuedJob instance
    """
    manager = RegistryManager(job.queue_name)
    manager.add_to_registry(job, 'scheduled', {
        'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
    })


def track_job_cancel(job):
    """Track job cancellation.

    Args:
        job: QueuedJob instance
    """
    manager = RegistryManager(job.queue_name)

    # Remove from any active registry
    for registry_type in ['started', 'scheduled', 'deferred']:
        manager.remove_from_registry(job, registry_type)

    # Add to canceled registry
    manager.add_to_registry(job, 'canceled', {
        'canceled_at': timezone.now().isoformat(),
    })
