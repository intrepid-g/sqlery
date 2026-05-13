"""Database cleanup and retention management."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .models import DaemonCommand, QueuedJob, JobRegistry
from .settings import get_setting


class CleanupManager:
    """Manage database cleanup and retention policies."""

    def __init__(self):
        """Initialize cleanup manager."""
        self.retention_config = get_setting('JOB_RETENTION', {})

    def cleanup_old_jobs(self, status=None, max_age_days=None, dry_run=False):
        """Remove old jobs based on age.

        Args:
            status: Job status to filter by (None = all statuses)
            max_age_days: Max age in days (default: from config)
            dry_run: If True, return count without deleting

        Returns:
            dict with deletion stats
        """
        if max_age_days is None:
            if status:
                max_age_days = self.retention_config.get(f'{status}_max_age_days', 30)
            else:
                max_age_days = self.retention_config.get('default_max_age_days', 30)

        cutoff = timezone.now() - timedelta(days=max_age_days)

        query = QueuedJob.objects.filter(created_at__lt=cutoff)
        if status:
            query = query.filter(status=status)

        count = query.count()

        if not dry_run:
            deleted_count, details = query.delete()
            return {
                'deleted': deleted_count,
                'details': details,
                'cutoff_date': cutoff.isoformat(),
            }
        else:
            return {
                'would_delete': count,
                'cutoff_date': cutoff.isoformat(),
            }

    def cleanup_by_count(self, status=None, keep_count=None, dry_run=False):
        """Keep only the most recent N jobs, delete older ones.

        Args:
            status: Job status to filter by (None = all statuses)
            keep_count: Number of jobs to keep (default: from config)
            dry_run: If True, return count without deleting

        Returns:
            dict with deletion stats
        """
        if keep_count is None:
            if status:
                keep_count = self.retention_config.get(f'{status}_max_count', 10000)
            else:
                keep_count = self.retention_config.get('default_max_count', 10000)

        query = QueuedJob.objects.all()
        if status:
            query = query.filter(status=status)

        query = query.order_by('-created_at')
        total_count = query.count()

        if total_count <= keep_count:
            return {
                'deleted': 0,
                'message': f'Total count ({total_count}) is within limit ({keep_count})',
            }

        # Get IDs of jobs to delete (all except the most recent keep_count)
        jobs_to_keep_ids = list(query.values_list('id', flat=True)[:keep_count])
        jobs_to_delete = QueuedJob.objects.exclude(id__in=jobs_to_keep_ids)

        if status:
            jobs_to_delete = jobs_to_delete.filter(status=status)

        count = jobs_to_delete.count()

        if not dry_run:
            deleted_count, details = jobs_to_delete.delete()
            return {
                'deleted': deleted_count,
                'details': details,
                'kept': keep_count,
            }
        else:
            return {
                'would_delete': count,
                'would_keep': keep_count,
            }

    def cleanup_old_registries(self, registry_type=None, max_age_days=None, dry_run=False):
        """Remove old registry entries.

        Args:
            registry_type: Registry type (None = all types)
            max_age_days: Max age in days (default: from config)
            dry_run: If True, return count without deleting

        Returns:
            dict with deletion stats
        """
        if max_age_days is None:
            registry_retention = get_setting('REGISTRY_RETENTION', {})
            if registry_type:
                max_age_days = registry_retention.get(registry_type, 7)
            else:
                max_age_days = 7

        cutoff = timezone.now() - timedelta(days=max_age_days)

        query = JobRegistry.objects.filter(entered_at__lt=cutoff)
        if registry_type:
            query = query.filter(registry_type=registry_type)

        count = query.count()

        if not dry_run:
            deleted_count, details = query.delete()
            return {
                'deleted': deleted_count,
                'details': details,
                'cutoff_date': cutoff.isoformat(),
            }
        else:
            return {
                'would_delete': count,
                'cutoff_date': cutoff.isoformat(),
            }

    def get_database_stats(self):
        """Get database size statistics.

        Returns:
            dict with database statistics
        """
        # from django.db import connection  # moved to top-level

        stats = {}

        # Job counts by status
        job_counts = (
            QueuedJob.objects
            .values('status')
            .annotate(count=Count('id'))
        )
        stats['job_counts'] = {item['status']: item['count'] for item in job_counts}
        stats['total_jobs'] = QueuedJob.objects.count()

        # Registry counts by type
        registry_counts = (
            JobRegistry.objects
            .values('registry_type')
            .annotate(count=Count('id'))
        )
        stats['registry_counts'] = {item['registry_type']: item['count'] for item in registry_counts}
        stats['total_registries'] = JobRegistry.objects.count()

        # Approximate table sizes (PostgreSQL)
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                # Get table sizes
                cursor.execute("""
                    SELECT
                        pg_size_pretty(pg_total_relation_size('sqlery_queued_job')) as jobs_size,
                        pg_size_pretty(pg_total_relation_size('sqlery_registry')) as registry_size
                """)
                row = cursor.fetchone()
                if row:
                    stats['jobs_table_size'] = row[0]
                    stats['registry_table_size'] = row[1]

        return stats

    def cleanup_per_job_ttl(self, dry_run=False):
        """Clean up jobs based on per-job result_ttl and failure_ttl.

        Deletes:
        - Successful jobs where finished_at + result_ttl < now (result_ttl != -1)
        - Failed jobs where finished_at + failure_ttl < now

        Args:
            dry_run: If True, return count without deleting

        Returns:
            dict with deletion stats
        """
        now = timezone.now()
        total_deleted = 0

        # Successful jobs with per-job result_ttl
        success_jobs = QueuedJob.objects.filter(
            status='success',
            result_ttl__isnull=False,
            finished_at__isnull=False,
        ).exclude(result_ttl=-1)  # -1 means keep forever

        for job in success_jobs:
            if job.finished_at + timedelta(seconds=job.result_ttl) < now:
                if not dry_run:
                    job.delete()
                total_deleted += 1

        # Failed/archived jobs with per-job failure_ttl
        failed_jobs = QueuedJob.objects.filter(
            status__in=['failed', 'archived'],
            failure_ttl__isnull=False,
            finished_at__isnull=False,
        ).exclude(failure_ttl=-1)  # -1 means keep forever

        for job in failed_jobs:
            if job.finished_at + timedelta(seconds=job.failure_ttl) < now:
                if not dry_run:
                    job.delete()
                total_deleted += 1

        return {
            'deleted' if not dry_run else 'would_delete': total_deleted,
        }

    def auto_cleanup(self, dry_run=False):
        """Run automatic cleanup based on configuration.

        Args:
            dry_run: If True, report what would be deleted without deleting

        Returns:
            dict with cleanup results
        """
        results = {
            'timestamp': timezone.now().isoformat(),
            'dry_run': dry_run,
            'actions': []
        }

        # Per-job TTL cleanup (before global retention)
        per_job_result = self.cleanup_per_job_ttl(dry_run=dry_run)
        if per_job_result.get('deleted', 0) > 0 or per_job_result.get('would_delete', 0) > 0:
            results['actions'].append({
                'action': 'cleanup_per_job_ttl',
                'result': per_job_result,
            })

        # Cleanup old daemon commands (processed commands older than 24h)
        try:
            stale_commands = DaemonCommand.objects.filter(
                created_at__lt=timezone.now() - timedelta(hours=24)
            ).exclude(status='pending')
            cmd_count = stale_commands.count()
            if not dry_run:
                stale_commands.delete()
            if cmd_count > 0:
                results['actions'].append({
                    'action': 'cleanup_daemon_commands',
                    'result': {'deleted' if not dry_run else 'would_delete': cmd_count},
                })
        except Exception:
            pass  # DaemonCommand table may not exist yet

        # Cleanup jobs by age
        for status in ['success', 'failed', 'archived']:
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
        for status in ['success', 'failed', 'archived']:
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
        if get_setting('AUTO_CLEANUP_REGISTRIES', True):
            registry_retention = get_setting('REGISTRY_RETENTION', {})
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

        return results

    def vacuum_database(self):
        """Run database vacuum/optimize (PostgreSQL only).

        Returns:
            dict with vacuum results
        """
        # from django.db import connection  # moved to top-level

        if connection.vendor != 'postgresql':
            return {
                'success': False,
                'message': 'VACUUM is only supported on PostgreSQL'
            }

        try:
            with connection.cursor() as cursor:
                cursor.execute('VACUUM ANALYZE sqlery_queued_job')
                cursor.execute('VACUUM ANALYZE sqlery_registry')

            return {
                'success': True,
                'message': 'Database tables vacuumed successfully'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
