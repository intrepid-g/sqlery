"""Django-agnostic RQ-compatible registry system for job lifecycle tracking."""

import logging
from datetime import datetime, timedelta, timezone

from ..compat import get_backend, get_config

logger = logging.getLogger(__name__)


class RegistryManager:
    """Manage job registries (RQ-compatible API).

    Tracks job lifecycle through different states: started, finished, failed,
    scheduled, deferred, canceled.

    Works in both Django and standalone modes via backend abstraction.
    """

    def __init__(self, queue_name: str = "default", backend=None):
        """Initialize registry manager for a queue.

        Args:
            queue_name: Queue name to filter jobs by
            backend: DatabaseBackend instance (auto-detected if not provided)
        """
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()

        self.backend = backend
        self.queue_name = queue_name

    def add_to_registry(self, job_id: int, registry_type: str, metadata: dict | None = None):
        """Add job to a registry.

        Args:
            job_id: Job ID
            registry_type: Registry type (started/finished/failed/etc)
            metadata: Optional metadata dict
        """
        self.backend.add_job_to_registry(
            job_id=job_id,
            registry_type=registry_type,
            metadata=metadata or {}
        )

    def remove_from_registry(self, job_id: int, registry_type: str):
        """Remove job from registry (mark as exited).

        Args:
            job_id: Job ID
            registry_type: Registry type to remove from
        """
        self.backend.remove_job_from_registry(job_id, registry_type)

    def get_registry(self, registry_type: str):
        """Get all active jobs in a registry.

        Args:
            registry_type: Registry type

        Returns:
            List of active registry entries
        """
        return self.backend.get_registry_jobs(
            registry_type=registry_type,
            queue_name=self.queue_name
        )

    # RQ-compatible methods

    def get_started_jobs(self):
        """Get all currently running jobs.

        Returns:
            List of started jobs
        """
        return self.get_registry('started')

    def get_finished_jobs(self, limit: int | None = None):
        """Get completed jobs.

        Args:
            limit: Optional limit on number of jobs returned

        Returns:
            List of finished jobs
        """
        jobs = self.get_registry('finished')
        return jobs[:limit] if limit else jobs

    def get_failed_jobs(self, limit: int | None = None):
        """Get failed jobs.

        Args:
            limit: Optional limit on number of jobs returned

        Returns:
            List of failed jobs
        """
        jobs = self.get_registry('failed')
        return jobs[:limit] if limit else jobs

    def get_scheduled_jobs(self):
        """Get scheduled jobs.

        Returns:
            List of scheduled jobs
        """
        return self.get_registry('scheduled')

    def get_deferred_jobs(self):
        """Get deferred jobs (waiting for dependencies).

        Returns:
            List of deferred jobs
        """
        return self.get_registry('deferred')

    def get_canceled_jobs(self):
        """Get canceled jobs.

        Returns:
            List of canceled jobs
        """
        return self.get_registry('canceled')

    def cleanup_registry(self, registry_type: str, max_age_days: int | None = None) -> int:
        """Remove old entries from registry.

        Args:
            registry_type: Registry type to clean
            max_age_days: Max age in days (default: from config)

        Returns:
            Number of entries deleted
        """
        # from ..compat import get_config  # moved to top-level

        if max_age_days is None:
            retention = get_config('REGISTRY_RETENTION', {})
            max_age_days = retention.get(registry_type, 7)

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        deleted_count = self.backend.cleanup_registry(
            registry_type=registry_type,
            cutoff_time=cutoff
        )

        logger.info(
            f"Cleaned up {deleted_count} entries from '{registry_type}' registry "
            f"older than {max_age_days} days"
        )

        return deleted_count

    def count(self, registry_type: str) -> int:
        """Count active entries in a registry.

        Args:
            registry_type: Registry type

        Returns:
            Number of active entries
        """
        return len(self.get_registry(registry_type))


def get_registry(registry_type: str, queue: str = 'default'):
    """Get a registry for a specific queue (RQ-compatible helper).

    Args:
        registry_type: Registry type (started/finished/failed/etc)
        queue: Queue name

    Returns:
        RegistryWrapper instance configured for the registry type
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
        job: Job instance
    """
    manager = RegistryManager(job.queue_name)
    manager.add_to_registry(job.id, 'started', {
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'worker_id': getattr(job, 'worker_id', None),
    })


def track_job_finish(job, status: str = 'success'):
    """Track job completion (move from started to finished/failed).

    Args:
        job: Job instance
        status: 'success' or 'failed'
    """
    manager = RegistryManager(job.queue_name)

    # Remove from started registry
    manager.remove_from_registry(job.id, 'started')

    # Add to appropriate completion registry
    if status == 'success':
        manager.add_to_registry(job.id, 'finished', {
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'duration': getattr(job, 'duration_seconds', None),
            'output': (job.output[:1000] if job.output else '') if hasattr(job, 'output') else '',
        })
    elif status == 'failed':
        manager.add_to_registry(job.id, 'failed', {
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'duration': getattr(job, 'duration_seconds', None),
            'error': (job.error[:1000] if job.error else '') if hasattr(job, 'error') else '',
            'traceback': (job.traceback[:2000] if job.traceback else '') if hasattr(job, 'traceback') else '',
        })


def track_job_schedule(job):
    """Track job being scheduled for later.

    Args:
        job: Job instance
    """
    manager = RegistryManager(job.queue_name)
    manager.add_to_registry(job.id, 'scheduled', {
        'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
    })


def track_job_cancel(job):
    """Track job cancellation.

    Args:
        job: Job instance
    """
    manager = RegistryManager(job.queue_name)

    # Remove from any active registry
    for registry_type in ['started', 'scheduled', 'deferred']:
        try:
            manager.remove_from_registry(job.id, registry_type)
        except Exception:
            pass  # Job may not be in this registry

    # Add to canceled registry
    manager.add_to_registry(job.id, 'canceled', {
        'canceled_at': datetime.now(timezone.utc).isoformat(),
    })
