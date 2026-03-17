# Migrating from django-tasks-scheduler to sqlery

Complete guide for migrating from django-tasks-scheduler to sqlery.

## Why Migrate?

**Benefits of switching to sqlery:**
- ✅ **Multi-worker support** - Run multiple workers for higher throughput
- ✅ **Job registries** - Track job lifecycle (RQ-compatible)
- ✅ **Automatic cleanup** - Database retention policies built-in
- ✅ **Daemon mode** - Continuous background worker
- ✅ **Real-time dashboard** - Live stats and monitoring
- ✅ **Better scalability** - Worker pool management
- ✅ **Active development** - Regular updates and new features

**When to stay with django-tasks-scheduler:**
- You have very light scheduling needs (<100 jobs/day)
- Single-worker is sufficient
- You want the absolute simplest solution

## Feature Mapping

| django-tasks-scheduler | sqlery |
|------------------------|-----------------|
| `@job` decorator | `@job` decorator (same name!) |
| `enqueue()` | `enqueue()` (same API!) |
| `enqueue_at()` | `enqueue_at()` (same API!) |
| `ScheduledTask` model | `ScheduledTask` model (compatible) |
| Django admin | Django admin (enhanced) |
| Single worker | **Multi-worker pool** |
| Manual cleanup | **Automatic cleanup** |
| Basic dashboard | **Real-time dashboard** |
| No registries | **RQ-compatible registries** |
| No daemon mode | **Continuous daemon** |

## Migration Steps

### Step 1: Assessment

Good news! The APIs are very similar. Migration is straightforward.

### Step 2: Install sqlery

```bash
# sqlery can run alongside django-tasks-scheduler during migration
pip install sqlery
```

### Step 3: Update Django Settings

**Add sqlery to existing setup:**

```python
# settings.py
INSTALLED_APPS = [
    # ...
    # 'django_tasks_scheduler',  # Keep during migration
    'sqlery',  # Add this
]

MIDDLEWARE = [
    # ...
    # Keep existing middleware during migration
    'sqlery.daemon_middleware.DaemonMiddleware',  # Add this
]

# Add sqlery configuration
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'DAEMON_CHECK_INTERVAL': 10,

    # Multi-worker (NEW capability!)
    'MAX_WORKERS_PER_NODE': 3,
    'WORKER_QUEUES': ['high', 'default', 'low'],
    'QUEUE_PRIORITIES': {
        'high': 100,
        'default': 50,
        'low': 10,
    },

    # Automatic cleanup (NEW capability!)
    'JOB_RETENTION': {
        'success_max_age_days': 7,
        'failed_max_age_days': 30,
    },
    'AUTO_CLEANUP_JOBS': True,

    # Job registries (NEW capability!)
    'ENABLE_REGISTRIES': True,
}
```

### Step 4: Run Migrations

```bash
# Apply sqlery migrations
python manage.py migrate sqlery
```

### Step 5: Update Task Definitions (Minimal Changes!)

The decorator syntax is almost identical!

**django-tasks-scheduler:**

```python
# OLD - django-tasks-scheduler
from django_tasks_scheduler import job

@job
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

@job(queue='high_priority')
def urgent_task():
    # ... task logic
    pass
```

**sqlery:**

```python
# NEW - sqlery (nearly identical!)
from sqlery import job

@job
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

@job(queue='high', priority=100)  # Added priority option
def urgent_task():
    # ... task logic
    pass
```

### Step 6: Update Enqueue Calls (Usually No Changes!)

**django-tasks-scheduler:**

```python
# OLD - django-tasks-scheduler
from myapp.tasks import send_email

# Enqueue
send_email.enqueue(to='user@example.com', subject='Hello')

# Delay (alias)
send_email.delay(to='user@example.com', subject='Hello')

# Schedule for later
from datetime import datetime, timezone, timedelta
run_at = datetime.now(timezone.utc) + timedelta(hours=1)
send_email.enqueue_at(run_at, to='user@example.com', subject='Hello')
```

**sqlery:**

```python
# NEW - sqlery (SAME API!)
from myapp.tasks import send_email

# Enqueue (identical)
send_email.enqueue(to='user@example.com', subject='Hello')

# Delay (identical)
send_email.delay(to='user@example.com', subject='Hello')

# Schedule for later (identical)
from datetime import datetime, timezone, timedelta
run_at = datetime.now(timezone.utc) + timedelta(hours=1)
send_email.enqueue_at(run_at, to='user@example.com', subject='Hello')
```

No code changes needed for enqueuing!

### Step 7: Migrate Scheduled Tasks

**Option A: Via Django Admin (Easiest)**

1. Go to django-tasks-scheduler admin
2. Note down all scheduled tasks (name, cron, task path, queue)
3. Go to sqlery admin
4. Create identical scheduled tasks
5. Disable old tasks

**Option B: Database Migration Script**

```python
# Migration script to copy scheduled tasks
from django_tasks_scheduler.models import ScheduledTask as OldScheduledTask
from sqlery.models import ScheduledTask as NewScheduledTask

# Copy all scheduled tasks
for old_task in OldScheduledTask.objects.filter(enabled=True):
    NewScheduledTask.objects.get_or_create(
        name=old_task.name,
        defaults={
            'task_path': old_task.task_path,
            'cron_expression': old_task.cron_expression,
            'queue_name': old_task.queue_name or 'default',
            'priority': old_task.priority or 0,
            'enabled': True,
        }
    )
    print(f"Migrated: {old_task.name}")
```

### Step 8: Start Multi-Worker Daemon

```bash
# Start sqlery daemon (with worker pool)
python manage.py daemon start

# Check status
python manage.py daemon status

# View workers
python manage.py workers list
```

## Code Comparison Examples

### Example 1: Simple Task (No Changes!)

**Before (django-tasks-scheduler):**

```python
from django_tasks_scheduler import job

@job
def daily_cleanup():
    # Cleanup logic
    return "Cleanup complete"

# Usage
daily_cleanup.enqueue()
```

**After (sqlery):**

```python
from sqlery import job

@job
def daily_cleanup():
    # Cleanup logic
    return "Cleanup complete"

# Usage (identical!)
daily_cleanup.enqueue()
```

### Example 2: Task with Queue

**Before (django-tasks-scheduler):**

```python
from django_tasks_scheduler import job

@job(queue='reports')
def generate_report(report_type):
    # Generate report
    return f"Report {report_type} generated"
```

**After (sqlery):**

```python
from sqlery import job

@job(queue='reports', priority=50)  # Can add priority
def generate_report(report_type):
    # Generate report
    return f"Report {report_type} generated"
```

### Example 3: Scheduled Task

Both use Django admin or code:

**django-tasks-scheduler:**

```python
from django_tasks_scheduler.models import ScheduledTask

ScheduledTask.objects.create(
    name='Daily Report',
    task_path='myapp.tasks.generate_daily_report',
    cron_expression='0 8 * * *',
    queue_name='reports',
    enabled=True
)
```

**sqlery (identical structure!):**

```python
from sqlery.models import ScheduledTask

ScheduledTask.objects.create(
    name='Daily Report',
    task_path='myapp.tasks.generate_daily_report',
    cron_expression='0 8 * * *',
    queue_name='reports',
    priority=5,  # Optional: add priority
    enabled=True
)
```

## New Capabilities

### Multi-Worker Processing

**django-tasks-scheduler:** Single worker only

**sqlery:** Worker pool for parallel processing

```python
# settings.py
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 5,  # 5 parallel workers!
}
```

```bash
# Check worker status
python manage.py workers list

# Example output:
# Active Workers: 5 / 5
#   Idle: 2, Busy: 3
```

### Job Registries

**django-tasks-scheduler:** No job lifecycle tracking

**sqlery:** Full lifecycle tracking

```python
from sqlery.models import JobRegistry

# See all running jobs
running = JobRegistry.objects.filter(registry_type='started')

# See all completed jobs
finished = JobRegistry.objects.filter(registry_type='finished')

# See all failed jobs
failed = JobRegistry.objects.filter(registry_type='failed')
```

### Automatic Database Cleanup

**django-tasks-scheduler:** Manual cleanup only

**sqlery:** Automatic retention policies

```python
# settings.py
DJANGO_SQL_JOBS = {
    'AUTO_CLEANUP_JOBS': True,
    'JOB_RETENTION': {
        'success_max_age_days': 7,  # Auto-delete after 7 days
        'failed_max_age_days': 30,  # Keep failures for 30 days
    },
}
```

```bash
# Manual cleanup also available
python manage.py cleanup_jobs auto
python manage.py cleanup_jobs stats
```

### Real-Time Dashboard

**django-tasks-scheduler:** Basic admin views

**sqlery:** Live dashboard with auto-refresh

Access at: `/admin/sqlery/dashboard/`

- Auto-refreshes every 3 seconds
- Live job counts by status
- Recent activity
- Queue statistics
- Worker status

## Gradual Migration Strategy

### Phase 1: Run Both Systems

```python
# settings.py - Both installed
INSTALLED_APPS = [
    'django_tasks_scheduler',
    'sqlery',
]

# Both middleware active
MIDDLEWARE = [
    'django_tasks_scheduler.middleware.SchedulerMiddleware',
    'sqlery.daemon_middleware.DaemonMiddleware',
]
```

### Phase 2: Migrate Tasks One by One

1. Update task imports: `from sqlery import job`
2. Update enqueue calls (usually no changes needed)
3. Test thoroughly

### Phase 3: Migrate Scheduled Tasks

1. Copy scheduled tasks to sqlery
2. Disable in django-tasks-scheduler
3. Monitor for 1-2 days

### Phase 4: Remove django-tasks-scheduler

```bash
# Stop using django-tasks-scheduler
pip uninstall django-tasks-scheduler

# Remove from INSTALLED_APPS and MIDDLEWARE
```

## Configuration Mapping

| django-tasks-scheduler Setting | sqlery Equivalent |
|--------------------------------|---------------------------|
| Default queue | `DJANGO_SQL_JOBS['DEFAULT_QUEUE']` |
| Check interval | `DJANGO_SQL_JOBS['DAEMON_CHECK_INTERVAL']` |
| (No equivalent) | `DJANGO_SQL_JOBS['MAX_WORKERS_PER_NODE']` (NEW) |
| (No equivalent) | `DJANGO_SQL_JOBS['JOB_RETENTION']` (NEW) |
| (No equivalent) | `DJANGO_SQL_JOBS['ENABLE_REGISTRIES']` (NEW) |

## Benefits Summary

After migration, you get:

✅ **Multi-worker** - Process jobs in parallel
✅ **Job registries** - Track job lifecycle
✅ **Automatic cleanup** - Database retention policies
✅ **Daemon mode** - Continuous background processing
✅ **Real-time dashboard** - Live monitoring
✅ **Worker management** - List, stop, kill, cleanup workers
✅ **Better scalability** - Handle higher throughput

## Common Issues

### Issue: Import error

**Problem:**
```python
from django_tasks_scheduler import job  # Old import
```

**Solution:**
```python
from sqlery import job  # New import
```

### Issue: Tasks not processing faster

**Problem:** Still processing one at a time

**Solution:** Enable multi-worker mode:

```python
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 3,  # Or 5, 10, etc.
}
```

### Issue: Database growing too large

**Problem:** Jobs table getting big

**Solution:** Enable automatic cleanup:

```python
DJANGO_SQL_JOBS = {
    'AUTO_CLEANUP_JOBS': True,
    'JOB_RETENTION': {
        'success_max_age_days': 7,
    },
}
```

## Performance Improvement

**django-tasks-scheduler:**
- Single worker
- Jobs process sequentially
- ~1 job at a time

**sqlery:**
- Multiple workers
- Jobs process in parallel
- 3-10 jobs simultaneously (configurable)

**Example:** 1000 jobs taking 1 second each
- django-tasks-scheduler: ~1000 seconds (16 minutes)
- sqlery (5 workers): ~200 seconds (3 minutes)

## Need Help?

- See [CONFIGURATION.md](CONFIGURATION.md) for settings reference
- See [MANAGEMENT_COMMANDS.md](MANAGEMENT_COMMANDS.md) for CLI commands
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues
- Open an issue on GitHub for migration-specific questions
