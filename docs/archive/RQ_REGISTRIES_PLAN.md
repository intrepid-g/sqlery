# RQ-Compatible Registries Implementation Plan

## Overview

RQ (Redis Queue) uses **Registries** to track jobs in different lifecycle states. This plan implements equivalent functionality using the Django database, providing RQ-compatible APIs while maintaining sqlery' philosophy of zero external dependencies.

## What Are Registries?

In RQ, registries are Redis sets that track job IDs by state. They provide:
- Fast lookups by job state
- Job lifecycle tracking
- Cleanup and maintenance operations
- Monitoring and observability

### RQ Registry Types

1. **StartedRegistry** - Currently running jobs
2. **FinishedRegistry** - Successfully completed jobs
3. **FailedRegistry** - Jobs that raised exceptions
4. **ScheduledRegistry** - Jobs scheduled for future execution
5. **DeferredRegistry** - Jobs waiting for dependencies
6. **CanceledRegistry** - Jobs canceled before execution

## Architecture

### Current State (sqlery)

```python
class QueuedJob(models.Model):
    status = models.CharField(
        choices=[
            ('queued', 'Queued'),
            ('running', 'Running'),
            ('success', 'Success'),
            ('failed', 'Failed'),
        ]
    )
```

Status is tracked in the job itself. Registries would add:
- Explicit lifecycle events
- Job state history
- Registry-based queries
- RQ-compatible APIs

### Proposed Implementation

#### Option 1: Virtual Registries (Database Views)

Use database queries to simulate registries (no schema changes):

```python
class JobRegistry:
    """Base registry class"""

    @property
    def started(self):
        """StartedRegistry equivalent"""
        return QueuedJob.objects.filter(status='running')

    @property
    def finished(self):
        """FinishedRegistry equivalent"""
        return QueuedJob.objects.filter(status='success')

    @property
    def failed(self):
        """FailedRegistry equivalent"""
        return QueuedJob.objects.filter(status='failed')

    @property
    def scheduled(self):
        """ScheduledRegistry equivalent"""
        from django.utils import timezone
        return QueuedJob.objects.filter(
            status='queued',
            scheduled_at__gt=timezone.now()
        )

    @property
    def canceled(self):
        """CanceledRegistry equivalent"""
        return QueuedJob.objects.filter(status='canceled')
```

**Pros:**
- No schema changes
- Simple implementation
- Backward compatible

**Cons:**
- No job state history
- Limited RQ compatibility
- No deferred/dependency tracking

#### Option 2: Explicit Registry Model (Recommended)

Add a separate model for tracking job lifecycle events:

```python
class JobRegistry(models.Model):
    """Track job lifecycle in registries"""

    job = models.ForeignKey('QueuedJob', on_delete=models.CASCADE)
    registry_type = models.CharField(
        max_length=20,
        choices=[
            ('started', 'Started'),
            ('finished', 'Finished'),
            ('failed', 'Failed'),
            ('scheduled', 'Scheduled'),
            ('deferred', 'Deferred'),
            ('canceled', 'Canceled'),
        ],
        db_index=True
    )
    entered_at = models.DateTimeField(auto_now_add=True)
    exited_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)  # Extra info per registry

    class Meta:
        indexes = [
            models.Index(fields=['registry_type', 'entered_at']),
            models.Index(fields=['job', 'registry_type']),
        ]
        ordering = ['-entered_at']
```

**Pros:**
- Full job lifecycle history
- RQ-compatible features
- Support for dependencies
- Detailed audit trail

**Cons:**
- Schema changes required
- More complex
- Additional storage

## Implementation Details

### Registry Manager

```python
class RegistryManager:
    """Manage job registries (RQ-compatible API)"""

    def __init__(self, queue_name='default'):
        self.queue_name = queue_name

    def add_to_registry(self, job, registry_type, metadata=None):
        """Add job to a registry"""
        JobRegistry.objects.create(
            job=job,
            registry_type=registry_type,
            metadata=metadata or {}
        )

    def remove_from_registry(self, job, registry_type):
        """Remove job from registry (mark as exited)"""
        JobRegistry.objects.filter(
            job=job,
            registry_type=registry_type,
            exited_at__isnull=True
        ).update(exited_at=timezone.now())

    def get_registry(self, registry_type):
        """Get all active jobs in a registry"""
        return JobRegistry.objects.filter(
            registry_type=registry_type,
            exited_at__isnull=True,
            job__queue_name=self.queue_name
        ).select_related('job')

    # RQ-compatible methods
    def get_started_jobs(self):
        """Get all currently running jobs"""
        return self.get_registry('started')

    def get_finished_jobs(self, limit=None):
        """Get completed jobs"""
        qs = self.get_registry('finished')
        return qs[:limit] if limit else qs

    def get_failed_jobs(self, limit=None):
        """Get failed jobs"""
        qs = self.get_registry('failed')
        return qs[:limit] if limit else qs

    def get_scheduled_jobs(self):
        """Get scheduled jobs"""
        return self.get_registry('scheduled')

    def cleanup_registry(self, registry_type, max_age_days=7):
        """Remove old entries from registry"""
        cutoff = timezone.now() - timedelta(days=max_age_days)
        JobRegistry.objects.filter(
            registry_type=registry_type,
            entered_at__lt=cutoff
        ).delete()
```

### Job Lifecycle Integration

Update executor to track registry changes:

```python
class TaskExecutor:
    def __init__(self):
        self.registry = RegistryManager()

    def process_job(self, job):
        """Process job with registry tracking"""
        try:
            # Move to StartedRegistry
            self.registry.add_to_registry(job, 'started')

            # Execute job
            result = self._execute_job(job)

            # Move to FinishedRegistry
            self.registry.remove_from_registry(job, 'started')
            self.registry.add_to_registry(job, 'finished', {
                'result': result,
                'duration': job.duration_seconds
            })

        except Exception as e:
            # Move to FailedRegistry
            self.registry.remove_from_registry(job, 'started')
            self.registry.add_to_registry(job, 'failed', {
                'error': str(e),
                'traceback': traceback.format_exc()
            })
            raise
```

### Scheduled Jobs Integration

```python
def schedule_job_for_later(job, execute_at):
    """Schedule job for future execution"""
    job.scheduled_at = execute_at
    job.save()

    # Add to ScheduledRegistry
    registry = RegistryManager(job.queue_name)
    registry.add_to_registry(job, 'scheduled', {
        'scheduled_at': execute_at.isoformat()
    })
```

### Job Cancellation

```python
def cancel_job(job_id):
    """Cancel a queued job"""
    job = QueuedJob.objects.get(id=job_id, status='queued')
    job.status = 'canceled'
    job.save()

    # Add to CanceledRegistry
    registry = RegistryManager(job.queue_name)
    registry.add_to_registry(job, 'canceled', {
        'canceled_at': timezone.now().isoformat(),
        'reason': 'User requested'
    })
```

### Deferred Jobs (Dependencies)

```python
class JobDependency(models.Model):
    """Track job dependencies"""
    job = models.ForeignKey('QueuedJob', on_delete=models.CASCADE)
    depends_on = models.ForeignKey(
        'QueuedJob',
        on_delete=models.CASCADE,
        related_name='dependents'
    )
    created_at = models.DateTimeField(auto_now_add=True)

def enqueue_with_dependency(task, depends_on_job):
    """Enqueue job that waits for another job"""
    job = QueuedJob.objects.create(
        task_path=task,
        status='queued'
    )

    # Create dependency
    JobDependency.objects.create(
        job=job,
        depends_on=depends_on_job
    )

    # Add to DeferredRegistry
    registry = RegistryManager()
    registry.add_to_registry(job, 'deferred', {
        'depends_on': depends_on_job.id
    })

    return job

def check_deferred_jobs():
    """Check if deferred jobs can now run"""
    registry = RegistryManager()
    deferred = registry.get_registry('deferred')

    for entry in deferred:
        job = entry.job
        # Check if all dependencies completed
        incomplete = JobDependency.objects.filter(
            job=job,
            depends_on__status__in=['queued', 'running']
        ).exists()

        if not incomplete:
            # Dependencies met, remove from deferred
            registry.remove_from_registry(job, 'deferred')
            # Job will be picked up by workers
```

## RQ-Compatible API

Provide API matching RQ's interface:

```python
# RQ-style registry access
from sqlery.registries import get_registry

# Get registries
started_registry = get_registry('started', queue='default')
failed_registry = get_registry('failed', queue='default')

# List jobs
for entry in started_registry.get_jobs():
    print(f"Job {entry.job.id}: {entry.job.task_path}")

# Cleanup old finished jobs
finished_registry = get_registry('finished')
finished_registry.cleanup(max_age_days=7)

# Get counts
print(f"Running: {started_registry.count()}")
print(f"Failed: {failed_registry.count()}")
```

## Dashboard Integration

Add registry views to dashboard:

```python
def dashboard_stats(request):
    registry = RegistryManager()

    return JsonResponse({
        # ... existing stats ...

        'registries': {
            'started': registry.get_started_jobs().count(),
            'finished': registry.get_finished_jobs().count(),
            'failed': registry.get_failed_jobs().count(),
            'scheduled': registry.get_scheduled_jobs().count(),
            'deferred': registry.get_registry('deferred').count(),
            'canceled': registry.get_registry('canceled').count(),
        },

        'recent_failures': [
            {
                'job_id': entry.job.id,
                'task': entry.job.task_path,
                'failed_at': entry.entered_at.isoformat(),
                'error': entry.metadata.get('error'),
            }
            for entry in registry.get_failed_jobs(limit=10)
        ]
    })
```

Dashboard UI:

```html
<!-- Registry Stats Cards -->
<div class="registries-grid">
    <div class="registry-card">
        <h3>Started Jobs</h3>
        <div class="count">{{ registries.started }}</div>
        <a href="/admin/sqlery/jobregistry/?registry_type=started">View</a>
    </div>

    <div class="registry-card failed">
        <h3>Failed Jobs</h3>
        <div class="count">{{ registries.failed }}</div>
        <a href="/admin/sqlery/jobregistry/?registry_type=failed">View</a>
    </div>

    <!-- ... other registries ... -->
</div>
```

## Management Commands

```bash
# List jobs in registry
python manage.py registry list started
python manage.py registry list failed --limit 10

# Cleanup old registry entries
python manage.py registry cleanup finished --days 7
python manage.py registry cleanup failed --days 30

# Retry failed jobs
python manage.py registry retry failed --limit 5

# Cancel scheduled jobs
python manage.py registry cancel <job-id>

# Show registry stats
python manage.py registry stats
```

## Configuration

```python
DJANGO_SQL_JOBS = {
    # Registry settings
    'ENABLE_REGISTRIES': True,  # Enable registry tracking
    'REGISTRY_RETENTION': {
        'finished': 7,   # Keep finished jobs for 7 days
        'failed': 30,    # Keep failed jobs for 30 days
        'started': 1,    # Clean up stale started entries after 1 day
        'canceled': 7,   # Keep canceled jobs for 7 days
    },
    'AUTO_CLEANUP_REGISTRIES': True,  # Auto-cleanup on daemon loop
}
```

## Migration from RQ

For teams migrating from RQ:

```python
# RQ code
from rq import Queue
from redis import Redis

redis_conn = Redis()
queue = Queue(connection=redis_conn)

# Check failed jobs
failed_registry = queue.failed_job_registry
for job_id in failed_registry.get_job_ids():
    job = Job.fetch(job_id, connection=redis_conn)
    print(job.exc_info)

# sqlery equivalent
from sqlery.registries import get_registry

failed_registry = get_registry('failed', queue='default')
for entry in failed_registry.get_jobs():
    print(entry.metadata.get('traceback'))
```

## Implementation Phases

### Phase 1: Core Registry Model
- [ ] Create `JobRegistry` model
- [ ] Add migration
- [ ] Implement `RegistryManager`
- [ ] Basic CRUD operations

### Phase 2: Lifecycle Integration
- [ ] Update `TaskExecutor` to track registry changes
- [ ] Add registry updates to job state transitions
- [ ] Implement automatic registry management

### Phase 3: Advanced Registries
- [ ] Implement deferred jobs (dependencies)
- [ ] Add job cancellation
- [ ] Scheduled jobs integration

### Phase 4: API & Compatibility
- [ ] RQ-compatible API
- [ ] Management commands
- [ ] Dashboard integration

### Phase 5: Maintenance & Cleanup
- [ ] Automatic registry cleanup
- [ ] Retention policies
- [ ] Performance optimization

## Benefits

### vs Current Implementation

| Feature | Current | With Registries |
|---------|---------|-----------------|
| Job lifecycle tracking | Basic (status field) | Detailed (event history) |
| Failed job inspection | Limited | Full traceback + metadata |
| Scheduled job visibility | Query | Dedicated registry |
| Job dependencies | ❌ | ✅ Deferred registry |
| RQ compatibility | ❌ | ✅ Compatible API |
| Audit trail | Partial | Complete history |

### Performance Considerations

**Database Impact:**
- Extra writes: 2-3 per job (enter/exit registries)
- Storage: ~100 bytes per registry entry
- Queries: Indexed, fast lookups

**Mitigation:**
- Automatic cleanup of old entries
- Configurable retention periods
- Batch operations for bulk registry updates

## Testing Strategy

```python
class RegistryTestCase(TestCase):
    def test_started_registry(self):
        """Test jobs appear in started registry"""
        job = self.create_job()
        executor = TaskExecutor()
        executor.start_job(job)

        registry = RegistryManager()
        started = registry.get_started_jobs()
        self.assertIn(job.id, [e.job.id for e in started])

    def test_failed_registry_metadata(self):
        """Test failed jobs include error metadata"""
        job = self.create_failing_job()
        executor = TaskExecutor()

        with self.assertRaises(Exception):
            executor.process_job(job)

        registry = RegistryManager()
        failed = registry.get_failed_jobs().first()
        self.assertEqual(failed.job.id, job.id)
        self.assertIn('error', failed.metadata)
        self.assertIn('traceback', failed.metadata)

    def test_job_dependencies(self):
        """Test deferred registry with dependencies"""
        job1 = self.create_job()
        job2 = enqueue_with_dependency('task', depends_on_job=job1)

        # Job2 should be in deferred
        registry = RegistryManager()
        deferred = registry.get_registry('deferred')
        self.assertEqual(deferred.count(), 1)

        # Complete job1
        job1.status = 'success'
        job1.save()
        check_deferred_jobs()

        # Job2 should no longer be deferred
        self.assertEqual(deferred.count(), 0)
```

## Future Enhancements

1. **Registry Webhooks**
   - Notify on failed jobs
   - Alerts for stuck jobs

2. **Registry Snapshots**
   - Point-in-time registry state
   - Historical analysis

3. **Custom Registries**
   - User-defined registry types
   - Domain-specific job tracking

4. **Cross-Queue Registries**
   - Global view of all queues
   - Multi-queue coordination
