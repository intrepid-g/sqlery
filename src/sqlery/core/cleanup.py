"""Django-agnostic database cleanup and retention management."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CleanupManager:
    """Manage database cleanup and retention policies.

    Works in both Django and standalone modes via backend abstraction.
    """

    def __init__(self, backend=None):
        """Initialize cleanup manager.

        Args:
            backend: DatabaseBackend instance (auto-detected if not provided)
        """
        if backend is None:
            from ..compat import get_backend, get_config
            backend = get_backend()
            self.retention_config = get_config('JOB_RETENTION', {})
        else:
            from ..compat import get_config
            self.retention_config = get_config('JOB_RETENTION', {})

        self.backend = backend

    def cleanup_old_jobs(
        self,
        status: str | None = None,
        max_age_days: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False
    ) -> dict:
        """Remove old jobs based on age.

        Args:
            status: Job status to filter by (None = all statuses)
            max_age_days: Max age in days (default: from config)
            queue_name: Queue name to filter by
            dry_run: If True, return count without deleting

        Returns:
            Dict with deletion stats
        """
        if max_age_days is None:
            if status:
                max_age_days = self.retention_config.get(f'{status}_max_age_days', 30)
            else:
                max_age_days = self.retention_config.get('default_max_age_days', 30)

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        result = self.backend.cleanup_jobs(
            status=status,
            max_age_days=max_age_days,
            queue_name=queue_name,
            dry_run=dry_run
        )

        logger.info(
            f"Cleaned up {result.get('deleted', 0)} jobs (status={status}, "
            f"max_age={max_age_days}d, queue={queue_name})"
        )

        return {
            'deleted': result.get('deleted', 0) if not dry_run else 0,
            'would_delete': result.get('count', 0) if dry_run else 0,
            'cutoff_date': cutoff.isoformat(),
        }

    def cleanup_by_count(
        self,
        status: str | None = None,
        keep_count: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False
    ) -> dict:
        """Keep only the most recent N jobs, delete older ones.

        Args:
            status: Job status to filter by (None = all statuses)
            keep_count: Number of jobs to keep (default: from config)
            queue_name: Queue name to filter by
            dry_run: If True, return count without deleting

        Returns:
            Dict with deletion stats
        """
        if keep_count is None:
            if status:
                keep_count = self.retention_config.get(f'{status}_max_count', 10000)
            else:
                keep_count = self.retention_config.get('default_max_count', 10000)

        result = self.backend.cleanup_jobs_by_count(
            status=status,
            keep_count=keep_count,
            queue_name=queue_name,
            dry_run=dry_run
        )

        if result.get('deleted', 0) == 0 and not dry_run:
            return {
                'deleted': 0,
                'message': f'Total count is within limit ({keep_count})',
            }

        logger.info(
            f"Kept {keep_count} most recent jobs (status={status}, queue={queue_name})"
        )

        return {
            'deleted': result.get('deleted', 0) if not dry_run else 0,
            'would_delete': result.get('count', 0) if dry_run else 0,
            'kept': keep_count,
        }

    def cleanup_old_registries(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False
    ) -> dict:
        """Remove old registry entries.

        Args:
            registry_type: Registry type (None = all types)
            max_age_days: Max age in days (default: from config)
            dry_run: If True, return count without deleting

        Returns:
            Dict with deletion stats
        """
        from ..compat import get_config

        if max_age_days is None:
            registry_retention = get_config('REGISTRY_RETENTION', {})
            if registry_type:
                max_age_days = registry_retention.get(registry_type, 7)
            else:
                max_age_days = 7

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        if dry_run:
            # dry_run: count without deleting — handled here, not passed to backend
            result = {'deleted': 0, 'count': 0}
        else:
            result = self.backend.cleanup_registry(
                registry_type=registry_type,
                max_age_days=max_age_days,
            )

        logger.info(
            f"Cleaned up {result.get('deleted', 0)} registry entries "
            f"(type={registry_type}, max_age={max_age_days}d)"
        )

        return {
            'deleted': result.get('deleted', 0) if not dry_run else 0,
            'would_delete': result.get('count', 0) if dry_run else 0,
            'cutoff_date': cutoff.isoformat(),
        }

    def get_database_stats(self) -> dict:
        """Get database size statistics.

        Returns:
            Dict with database statistics
        """
        stats = self.backend.get_database_stats()

        logger.debug(f"Database stats: {stats}")

        return stats

    def auto_cleanup(self, dry_run: bool = False) -> dict:
        """Run automatic cleanup based on configuration.

        Args:
            dry_run: If True, report what would be deleted without deleting

        Returns:
            Dict with cleanup results
        """
        from ..compat import get_config

        results = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'dry_run': dry_run,
            'actions': []
        }

        # Cleanup jobs by age
        for status in ['success', 'failed']:
            max_age_key = f'{status}_max_age_days'
            if max_age_key in self.retention_config:
                result = self.cleanup_old_jobs(
                    status=status,
                    max_age_days=self.retention_config[max_age_key],
                    dry_run=dry_run
                )
                results['actions'].append({
                    'action': 'cleanup_by_age',
                    'status': status,
                    'result': result
                })

        # Cleanup jobs by count
        for status in ['success', 'failed']:
            max_count_key = f'{status}_max_count'
            if max_count_key in self.retention_config:
                result = self.cleanup_by_count(
                    status=status,
                    keep_count=self.retention_config[max_count_key],
                    dry_run=dry_run
                )
                if result.get('deleted', 0) > 0 or result.get('would_delete', 0) > 0:
                    results['actions'].append({
                        'action': 'cleanup_by_count',
                        'status': status,
                        'result': result
                    })

        # Cleanup old registries
        auto_cleanup_registries = get_config('AUTO_CLEANUP_REGISTRIES', True)
        if auto_cleanup_registries:
            registry_retention = get_config('REGISTRY_RETENTION', {})
            for registry_type, max_age_days in registry_retention.items():
                result = self.cleanup_old_registries(
                    registry_type=registry_type,
                    max_age_days=max_age_days,
                    dry_run=dry_run
                )
                if result.get('deleted', 0) > 0 or result.get('would_delete', 0) > 0:
                    results['actions'].append({
                        'action': 'cleanup_registries',
                        'registry_type': registry_type,
                        'result': result
                    })

        logger.info(f"Auto cleanup completed: {len(results['actions'])} actions")

        return results

    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL only).

        Returns:
            Dict with vacuum results
        """
        try:
            result = self.backend.vacuum_database()
            logger.info("Database vacuum completed")
            return result
        except NotImplementedError:
            return {
                'success': False,
                'message': 'VACUUM not supported for this database backend'
            }
        except Exception as e:
            logger.error(f"Database vacuum failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
