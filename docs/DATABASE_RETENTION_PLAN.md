# Database Retention & Cleanup Plan

## Problem Statement

Without proper retention policies, the job queue tables will grow indefinitely:
- **QueuedJob** table accumulates completed/failed jobs
- **JobRegistry** entries pile up (if implemented)
- **ScheduledTask** history grows
- Database size increases
- Query performance degrades
- Disk space issues

## Solution: Automated Retention Policies

Implement configurable retention policies with multiple enforcement strategies:
1. **Age-based**: Delete records older than N days
2. **Count-based**: Keep only last N records per queue/status
3. **Size-based**: Enforce table size limits
4. **Hybrid**: Combine multiple policies

## Retention Policy Configuration

```python
DJANGO_SQL_JOBS = {
    # Enable automatic cleanup
    'AUTO_CLEANUP': True,
    'CLEANUP_INTERVAL_HOURS': 24,  # Run cleanup daily

    # Age-based retention (days)
    'RETENTION_DAYS': {
        'queued': 1,      # Delete old queued jobs after 1 day (likely stuck)
        'running': 1,     # Delete old running jobs after 1 day (likely crashed)
        'success': 7,     # Keep successful jobs for 7 days
        'failed': 30,     # Keep failed jobs for 30 days (debugging)
    },

    # Count-based retention (per queue)
    'RETENTION_COUNTS': {
        'success': 1000,  # Keep last 1000 successful jobs per queue
        'failed': 500,    # Keep last 500 failed jobs per queue
    },

    # Size-based retention (MB)
    'MAX_TABLE_SIZE_MB': {
        'queued_job': 1000,  # Max 1GB for jobs table
    },

    # Archive before deletion
    'ARCHIVE_BEFORE_DELETE': True,
    'ARCHIVE_TO': 'filesystem',  # 'filesystem', 's3', 'database'
    'ARCHIVE_PATH': '/var/lib/sqlery/archives/',

    # Scheduled task history
    'KEEP_TASK_HISTORY': True,
    'TASK_HISTORY_DAYS': 90,  # Keep 90 days of task run history
}
```

## Architecture

### Cleanup Manager

```python
class CleanupManager:
    """Manage database cleanup and retention policies"""

    def __init__(self):
        self.policies = self._load_policies()

    def run_cleanup(self):
        """Run all cleanup policies"""
        logger.info("Starting database cleanup...")

        stats = {
            'jobs_deleted': 0,
            'jobs_archived': 0,
            'registries_deleted': 0,
            'space_freed_mb': 0,
        }

        # Run each cleanup policy
        stats['jobs_deleted'] += self.cleanup_by_age()
        stats['jobs_deleted'] += self.cleanup_by_count()
        stats['jobs_deleted'] += self.cleanup_by_size()
        stats['registries_deleted'] += self.cleanup_registries()

        logger.info(f"Cleanup complete: {stats}")
        return stats

    def cleanup_by_age(self):
        """Delete old jobs based on age policies"""
        from django.utils import timezone
        from datetime import timedelta

        retention_days = get_setting('RETENTION_DAYS', {})
        deleted_count = 0

        for status, days in retention_days.items():
            cutoff_date = timezone.now() - timedelta(days=days)

            # Optional: Archive before delete
            if get_setting('ARCHIVE_BEFORE_DELETE', False):
                jobs_to_archive = QueuedJob.objects.filter(
                    status=status,
                    finished_at__lt=cutoff_date
                )
                self.archive_jobs(jobs_to_archive)

            # Delete old jobs
            deleted, _ = QueuedJob.objects.filter(
                status=status,
                finished_at__lt=cutoff_date
            ).delete()

            deleted_count += deleted
            logger.info(f"Deleted {deleted} {status} jobs older than {days} days")

        return deleted_count

    def cleanup_by_count(self):
        """Keep only last N jobs per queue/status"""
        retention_counts = get_setting('RETENTION_COUNTS', {})
        deleted_count = 0

        for status, max_count in retention_counts.items():
            # Get all queues
            queues = QueuedJob.objects.values_list('queue_name', flat=True).distinct()

            for queue_name in queues:
                # Get IDs to keep (most recent N)
                keep_ids = QueuedJob.objects.filter(
                    queue_name=queue_name,
                    status=status
                ).order_by('-finished_at').values_list('id', flat=True)[:max_count]

                # Delete everything else
                deleted, _ = QueuedJob.objects.filter(
                    queue_name=queue_name,
                    status=status
                ).exclude(id__in=keep_ids).delete()

                if deleted > 0:
                    deleted_count += deleted
                    logger.info(
                        f"Deleted {deleted} {status} jobs from '{queue_name}' "
                        f"(keeping {max_count})"
                    )

        return deleted_count

    def cleanup_by_size(self):
        """Enforce table size limits"""
        max_sizes = get_setting('MAX_TABLE_SIZE_MB', {})
        deleted_count = 0

        for table_name, max_size_mb in max_sizes.items():
            if table_name == 'queued_job':
                current_size_mb = self.get_table_size_mb('sqlery_queuedjob')

                if current_size_mb > max_size_mb:
                    # Calculate how much to delete
                    excess_mb = current_size_mb - max_size_mb
                    percent_to_delete = (excess_mb / current_size_mb) * 100

                    # Delete oldest jobs (success/failed only)
                    total_jobs = QueuedJob.objects.filter(
                        status__in=['success', 'failed']
                    ).count()

                    jobs_to_delete = int(total_jobs * (percent_to_delete / 100))

                    deleted, _ = QueuedJob.objects.filter(
                        status__in=['success', 'failed']
                    ).order_by('finished_at')[:jobs_to_delete].delete()

                    deleted_count += deleted
                    logger.info(
                        f"Deleted {deleted} jobs to reduce table size "
                        f"from {current_size_mb}MB to ~{max_size_mb}MB"
                    )

        return deleted_count

    def cleanup_registries(self):
        """Clean up old registry entries"""
        if not get_setting('ENABLE_REGISTRIES', False):
            return 0

        retention = get_setting('REGISTRY_RETENTION', {})
        deleted_count = 0

        for registry_type, days in retention.items():
            cutoff = timezone.now() - timedelta(days=days)
            deleted, _ = JobRegistry.objects.filter(
                registry_type=registry_type,
                entered_at__lt=cutoff
            ).delete()

            deleted_count += deleted
            if deleted > 0:
                logger.info(f"Deleted {deleted} {registry_type} registry entries")

        return deleted_count

    def get_table_size_mb(self, table_name):
        """Get table size in MB (PostgreSQL)"""
        from django.db import connection

        with connection.cursor() as cursor:
            # PostgreSQL
            if connection.vendor == 'postgresql':
                cursor.execute(
                    "SELECT pg_total_relation_size(%s) / 1024 / 1024 AS size_mb",
                    [table_name]
                )
                result = cursor.fetchone()
                return result[0] if result else 0

            # SQLite
            elif connection.vendor == 'sqlite':
                cursor.execute(
                    "SELECT page_count * page_size / 1024 / 1024 AS size_mb "
                    "FROM pragma_page_count(), pragma_page_size()"
                )
                result = cursor.fetchone()
                return result[0] if result else 0

            else:
                logger.warning(f"Table size check not supported for {connection.vendor}")
                return 0
```

### Archive System

```python
class ArchiveManager:
    """Archive jobs before deletion"""

    def archive_to_filesystem(self, jobs_queryset):
        """Archive jobs as JSON to filesystem"""
        archive_path = Path(get_setting('ARCHIVE_PATH', '/tmp/job-archives'))
        archive_path.mkdir(parents=True, exist_ok=True)

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        filename = archive_path / f'jobs_{timestamp}.jsonl'

        with open(filename, 'w') as f:
            for job in jobs_queryset.iterator(chunk_size=1000):
                archive_data = {
                    'id': job.id,
                    'task_path': job.task_path,
                    'status': job.status,
                    'queue_name': job.queue_name,
                    'created_at': job.created_at.isoformat(),
                    'finished_at': job.finished_at.isoformat() if job.finished_at else None,
                    'duration_seconds': job.duration_seconds,
                    'output': job.output,
                    'error': job.error,
                    'traceback': job.traceback,
                    'runs': job.runs,
                }
                f.write(json.dumps(archive_data) + '\n')

        # Compress archive
        self._compress_archive(filename)

        logger.info(f"Archived {jobs_queryset.count()} jobs to {filename}")

    def archive_to_s3(self, jobs_queryset):
        """Archive jobs to S3"""
        import boto3

        s3 = boto3.client('s3')
        bucket = get_setting('ARCHIVE_S3_BUCKET')
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        key = f'job-archives/{timestamp}.jsonl.gz'

        # Create compressed JSON
        import gzip
        data = '\n'.join([
            json.dumps(self._job_to_dict(job))
            for job in jobs_queryset.iterator(chunk_size=1000)
        ])
        compressed = gzip.compress(data.encode())

        # Upload to S3
        s3.put_object(Bucket=bucket, Key=key, Body=compressed)
        logger.info(f"Archived {jobs_queryset.count()} jobs to s3://{bucket}/{key}")

    def archive_to_database(self, jobs_queryset):
        """Archive to separate archive table"""
        # Create JobArchive model (cold storage table)
        for job in jobs_queryset.iterator(chunk_size=1000):
            JobArchive.objects.create(
                original_id=job.id,
                task_path=job.task_path,
                status=job.status,
                archived_at=timezone.now(),
                data=self._job_to_dict(job),
            )

        logger.info(f"Archived {jobs_queryset.count()} jobs to archive table")

    def _compress_archive(self, filename):
        """Compress archive file"""
        import gzip
        import shutil

        with open(filename, 'rb') as f_in:
            with gzip.open(f'{filename}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Remove original
        filename.unlink()

    def _job_to_dict(self, job):
        """Convert job to dictionary for archival"""
        return {
            'id': job.id,
            'task_path': job.task_path,
            'status': job.status,
            'queue_name': job.queue_name,
            'priority': job.priority,
            'created_at': job.created_at.isoformat(),
            'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'duration_seconds': job.duration_seconds,
            'output': job.output,
            'error': job.error,
            'traceback': job.traceback,
            'retry_count': job.retry_count,
            'max_retries': job.max_retries,
            'runs': job.runs,
        }
```

### Scheduled Cleanup Job

Integrate cleanup into daemon:

```python
# In daemon_worker.py

def run_daemon():
    """Main daemon loop with periodic cleanup"""
    cleanup_manager = CleanupManager()
    last_cleanup = timezone.now()
    cleanup_interval_hours = get_setting('CLEANUP_INTERVAL_HOURS', 24)

    while not shutdown_requested:
        # ... existing job processing ...

        # Check if cleanup is due
        if timezone.now() - last_cleanup > timedelta(hours=cleanup_interval_hours):
            try:
                cleanup_manager.run_cleanup()
                last_cleanup = timezone.now()
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")

        # ... rest of loop ...
```

### Management Commands

```bash
# Run cleanup manually
python manage.py cleanup_jobs --dry-run
python manage.py cleanup_jobs --force

# Cleanup specific status
python manage.py cleanup_jobs --status success --days 7

# Archive without deleting
python manage.py archive_jobs --status success --days 30

# Show retention stats
python manage.py retention_stats

# Restore from archive
python manage.py restore_archive /path/to/archive.jsonl.gz
```

Implementation:

```python
# management/commands/cleanup_jobs.py

class Command(BaseCommand):
    help = 'Clean up old jobs based on retention policies'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--status', type=str)
        parser.add_argument('--days', type=int)

    def handle(self, *args, **options):
        cleanup = CleanupManager()

        if options['dry_run']:
            self.stdout.write("DRY RUN - no jobs will be deleted")
            # Show what would be deleted
            self.show_cleanup_preview()
        else:
            if not options['force']:
                confirm = input("This will permanently delete jobs. Continue? (y/N) ")
                if confirm.lower() != 'y':
                    self.stdout.write("Aborted")
                    return

            stats = cleanup.run_cleanup()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleanup complete:\n"
                    f"  Jobs deleted: {stats['jobs_deleted']}\n"
                    f"  Jobs archived: {stats['jobs_archived']}\n"
                    f"  Registries deleted: {stats['registries_deleted']}"
                )
            )
```

## Monitoring & Alerts

```python
class RetentionMonitor:
    """Monitor retention policies and alert on issues"""

    def check_table_growth(self):
        """Alert if tables growing too fast"""
        current_size = self.get_table_size_mb('sqlery_queuedjob')
        max_size = get_setting('MAX_TABLE_SIZE_MB', {}).get('queued_job', float('inf'))

        if current_size > max_size * 0.9:  # 90% threshold
            self.send_alert(
                f"Job table approaching size limit: {current_size}MB / {max_size}MB"
            )

    def check_old_stuck_jobs(self):
        """Alert on jobs stuck in running state"""
        stuck_threshold = timezone.now() - timedelta(hours=24)
        stuck_jobs = QueuedJob.objects.filter(
            status='running',
            started_at__lt=stuck_threshold
        ).count()

        if stuck_jobs > 0:
            self.send_alert(f"{stuck_jobs} jobs stuck in running state for >24h")

    def check_failed_job_rate(self):
        """Alert on high failure rate"""
        hour_ago = timezone.now() - timedelta(hours=1)
        recent_jobs = QueuedJob.objects.filter(finished_at__gte=hour_ago)

        total = recent_jobs.count()
        failed = recent_jobs.filter(status='failed').count()

        if total > 0:
            failure_rate = (failed / total) * 100
            if failure_rate > 20:  # 20% threshold
                self.send_alert(f"High failure rate: {failure_rate:.1f}% in last hour")

    def send_alert(self, message):
        """Send alert (email, Slack, etc.)"""
        logger.warning(f"RETENTION ALERT: {message}")
        # Implement your alerting logic here
```

## Dashboard Integration

Add retention metrics to dashboard:

```python
def dashboard_stats(request):
    # ... existing stats ...

    # Retention stats
    retention_stats = {
        'table_sizes': {
            'jobs': cleanup.get_table_size_mb('sqlery_queuedjob'),
            'registries': cleanup.get_table_size_mb('sqlery_jobregistry'),
        },
        'job_counts': {
            status: QueuedJob.objects.filter(status=status).count()
            for status in ['queued', 'running', 'success', 'failed']
        },
        'oldest_jobs': {
            'success': QueuedJob.objects.filter(status='success').order_by('finished_at').first(),
            'failed': QueuedJob.objects.filter(status='failed').order_by('finished_at').first(),
        },
        'next_cleanup': get_next_cleanup_time(),
    }

    return JsonResponse({
        # ... existing data ...
        'retention': retention_stats,
    })
```

Dashboard UI:

```html
<!-- Retention Stats Panel -->
<div class="retention-panel">
    <h2>Database Health</h2>

    <div class="metrics">
        <div class="metric">
            <label>Jobs Table Size</label>
            <div class="value">{{ retention.table_sizes.jobs }} MB</div>
            <div class="progress-bar">
                <div class="progress" style="width: {{ size_percent }}%"></div>
            </div>
        </div>

        <div class="metric">
            <label>Next Cleanup</label>
            <div class="value">{{ retention.next_cleanup|timeuntil }}</div>
        </div>

        <div class="metric">
            <label>Oldest Successful Job</label>
            <div class="value">{{ retention.oldest_jobs.success.finished_at|timesince }} ago</div>
        </div>
    </div>

    <button onclick="runCleanup()">Run Cleanup Now</button>
</div>
```

## Testing

```python
class RetentionTestCase(TestCase):
    def test_age_based_cleanup(self):
        """Test jobs are deleted based on age"""
        # Create old job
        old_job = QueuedJob.objects.create(
            status='success',
            finished_at=timezone.now() - timedelta(days=10)
        )

        cleanup = CleanupManager()
        cleanup.cleanup_by_age()

        # Job should be deleted
        self.assertFalse(QueuedJob.objects.filter(id=old_job.id).exists())

    def test_count_based_cleanup(self):
        """Test only last N jobs are kept"""
        # Create 100 successful jobs
        for i in range(100):
            QueuedJob.objects.create(
                status='success',
                finished_at=timezone.now() - timedelta(days=i)
            )

        cleanup = CleanupManager()
        cleanup.cleanup_by_count()

        # Should keep only configured amount
        max_count = get_setting('RETENTION_COUNTS', {}).get('success', 1000)
        self.assertEqual(QueuedJob.objects.filter(status='success').count(), max_count)

    def test_archive_before_delete(self):
        """Test jobs are archived before deletion"""
        job = QueuedJob.objects.create(
            status='success',
            finished_at=timezone.now() - timedelta(days=10)
        )

        cleanup = CleanupManager()
        archive = ArchiveManager()

        # Archive and delete
        jobs_to_clean = QueuedJob.objects.filter(id=job.id)
        archive.archive_to_filesystem(jobs_to_clean)
        jobs_to_clean.delete()

        # Job deleted
        self.assertFalse(QueuedJob.objects.filter(id=job.id).exists())

        # Archive file exists
        archive_files = list(Path(get_setting('ARCHIVE_PATH')).glob('*.jsonl.gz'))
        self.assertTrue(len(archive_files) > 0)
```

## Implementation Phases

### Phase 1: Basic Cleanup
- [ ] Create `CleanupManager` class
- [ ] Implement age-based retention
- [ ] Add management command
- [ ] Basic testing

### Phase 2: Advanced Policies
- [ ] Count-based retention
- [ ] Size-based retention
- [ ] Table size monitoring

### Phase 3: Archival
- [ ] Filesystem archival
- [ ] S3 archival (optional)
- [ ] Archive restoration

### Phase 4: Automation
- [ ] Integrate with daemon
- [ ] Scheduled cleanup
- [ ] Dashboard integration

### Phase 5: Monitoring
- [ ] Retention monitoring
- [ ] Alerting system
- [ ] Performance metrics

## Best Practices

1. **Start Conservative**
   - Begin with longer retention periods
   - Monitor and adjust based on needs

2. **Archive Important Jobs**
   - Always archive failed jobs (debugging)
   - Archive successful jobs for compliance

3. **Test Cleanup Policies**
   - Use `--dry-run` first
   - Verify archives are valid

4. **Monitor Performance**
   - Watch cleanup duration
   - Ensure cleanup doesn't block workers

5. **Document Policies**
   - Document retention requirements
   - Compliance/regulatory needs

## Performance Impact

**Cleanup Operation:**
- Time: ~1-5 seconds per 1000 jobs deleted
- CPU: Low (database does the work)
- Locks: Brief table locks during deletion

**Recommendation:**
- Run cleanup during low-traffic periods
- Use batch deletion (chunks of 1000)
- Monitor slow query log

## Future Enhancements

1. **Intelligent Retention**
   - Keep failed jobs longer
   - Keep jobs with specific tags

2. **Compression**
   - Compress old jobs in-place
   - Reduces storage without deletion

3. **Tiered Storage**
   - Hot: Recent jobs in main table
   - Cold: Old jobs in archive table
   - Frozen: S3/Glacier for long-term

4. **Selective Archival**
   - User-defined archive rules
   - Tag-based retention
