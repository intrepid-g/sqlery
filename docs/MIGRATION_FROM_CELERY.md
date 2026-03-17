# Migrating from Celery to sqlery

Complete guide for migrating from Celery to sqlery.

## Why Migrate?

**Benefits of switching to sqlery:**
- ✅ **Simpler architecture** - No separate broker (Redis/RabbitMQ)
- ✅ **Lower complexity** - No Celery Beat, Flower, or multiple processes
- ✅ **Django-native** - Seamless ORM integration
- ✅ **Serverless support** - Run on AWS Lambda, Cloud Run
- ✅ **Built-in UI** - Django admin integration out of the box
- ✅ **Easy deployment** - Middleware or daemon, no complex setup

**When to stay with Celery:**
- ❌ You need task chains, groups, or chords (complex workflows)
- ❌ You have multi-datacenter distributed systems
- ❌ You need Canvas (advanced task composition)

## Feature Mapping

| Celery Feature | sqlery Equivalent |
|----------------|---------------------------|
| `@task` decorator | `@job` decorator |
| `task.delay()` | `job.delay()` or `job.enqueue()` |
| `task.apply_async()` | `job.enqueue()` with options |
| `task.apply_async(eta=...)` | `job.enqueue_at(time)` |
| Celery Beat | Built-in cron scheduling |
| `celery worker` | `python manage.py daemon start` |
| Flower dashboard | Built-in Django admin dashboard |
| Task retry | Built-in with exponential backoff |
| Task routing | Queue routing with priorities |
| Result backend | Database (automatic) |
| **NOT SUPPORTED** | Task chains, groups, chords |

## Migration Steps

### Step 1: Assessment

Identify if you use advanced Celery features:

```python
# Check your codebase for these patterns:
# - chain(), group(), chord() - NOT SUPPORTED
# - subtask(), signature() - NOT SUPPORTED
# - Task.replace(), Task.retry() - Use @job(max_retries=N) instead

# If you use these, you may need to refactor or stay with Celery
```

### Step 2: Install sqlery

```bash
# Remove Celery dependencies
pip uninstall celery celery-beat flower

# Install sqlery
pip install sqlery
```

### Step 3: Update Django Settings

**Remove Celery configuration:**

```python
# OLD - Celery configuration
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULE = {
    'daily-report': {
        'task': 'myapp.tasks.generate_daily_report',
        'schedule': crontab(hour=8, minute=0),
    },
}
```

**Add sqlery configuration:**

```python
# NEW - sqlery configuration
INSTALLED_APPS = [
    # ...
    'sqlery',
]

MIDDLEWARE = [
    # ...
    'sqlery.daemon_middleware.DaemonMiddleware',
]

DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'DAEMON_CHECK_INTERVAL': 10,

    # Multi-worker (equivalent to multiple Celery workers)
    'MAX_WORKERS_PER_NODE': 3,
    'WORKER_QUEUES': ['high', 'default', 'low'],

    # Retry defaults
    'DEFAULT_MAX_RETRIES': 3,
    'DEFAULT_RETRY_BACKOFF': 1.0,
}
```

### Step 4: Update Task Definitions

**Celery tasks:**

```python
# OLD - Celery
from celery import shared_task
from celery.exceptions import Retry

@shared_task
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

@shared_task(bind=True, max_retries=3)
def unreliable_task(self):
    try:
        # ... task logic
        pass
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)

@shared_task(queue='high_priority')
def urgent_task():
    # ... task logic
    pass
```

**sqlery tasks:**

```python
# NEW - sqlery
from sqlery import job

@job
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

@job(max_retries=3, retry_backoff=1.0)
def unreliable_task():
    # ... task logic
    # Automatic retry on exception, no need for try/except
    pass

@job(queue='high', priority=100)
def urgent_task():
    # ... task logic
    pass
```

### Step 5: Update Task Enqueuing

**Celery enqueuing:**

```python
# OLD - Celery
from myapp.tasks import send_email

# Basic
send_email.delay('user@example.com', 'Hello')

# With options
send_email.apply_async(
    args=['user@example.com', 'Hello'],
    queue='email',
    priority=5,
    countdown=3600,  # 1 hour delay
)

# At specific time
from datetime import datetime, timedelta
eta = datetime.utcnow() + timedelta(hours=1)
send_email.apply_async(
    args=['user@example.com', 'Hello'],
    eta=eta
)
```

**sqlery enqueuing:**

```python
# NEW - sqlery
from myapp.tasks import send_email

# Basic (same as Celery!)
send_email.delay('user@example.com', 'Hello')

# Or use .enqueue() (clearer)
send_email.enqueue(to='user@example.com', subject='Hello')

# With options (uses kwargs, not args)
send_email.enqueue(
    to='user@example.com',
    subject='Hello',
    queue='email',
    priority=5
)

# At specific time
from datetime import datetime, timezone, timedelta
run_at = datetime.now(timezone.utc) + timedelta(hours=1)
send_email.enqueue_at(
    run_at,
    to='user@example.com',
    subject='Hello'
)
```

### Step 6: Update Worker Management

**Celery workers:**

```bash
# OLD - Celery workers
celery -A myproject worker -l info

# With multiple queues
celery -A myproject worker -Q high_priority,default,low_priority

# Celery Beat for scheduled tasks
celery -A myproject beat -l info

# Flower for monitoring
celery -A myproject flower
```

**sqlery workers:**

```bash
# NEW - Single daemon command (manages everything)
python manage.py daemon start

# No separate processes needed!
# Scheduling, workers, and monitoring all integrated

# Check status
python manage.py daemon status
python manage.py workers list

# Built-in dashboard at /admin/sqlery/dashboard/
```

### Step 7: Migrate Scheduled Tasks (from Celery Beat)

**Celery Beat:**

```python
# OLD - Celery Beat in settings.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'daily-report': {
        'task': 'myapp.tasks.generate_daily_report',
        'schedule': crontab(hour=8, minute=0),
        'options': {'queue': 'reports'}
    },
    'hourly-cleanup': {
        'task': 'myapp.tasks.cleanup_old_files',
        'schedule': crontab(minute=0),
    },
}
```

**sqlery scheduled tasks:**

Create via Django Admin → Scheduled Tasks:

```python
# NEW - sqlery (via Django Admin or code)
from sqlery.models import ScheduledTask

ScheduledTask.objects.create(
    name='Daily Report',
    task_path='myapp.tasks.generate_daily_report',
    cron_expression='0 8 * * *',  # Same as crontab(hour=8, minute=0)
    queue_name='reports',
    enabled=True
)

ScheduledTask.objects.create(
    name='Hourly Cleanup',
    task_path='myapp.tasks.cleanup_old_files',
    cron_expression='0 * * * *',  # Same as crontab(minute=0)
    queue_name='default',
    enabled=True
)
```

## Code Comparison Examples

### Example 1: Simple Email Task

**Before (Celery):**

```python
from celery import shared_task
from django.core.mail import send_mail

@shared_task
def send_welcome_email(user_id):
    user = User.objects.get(id=user_id)
    send_mail(
        'Welcome!',
        'Welcome to our app',
        'noreply@example.com',
        [user.email]
    )
    return f"Email sent to {user.email}"

# Usage
send_welcome_email.delay(user_id=123)
```

**After (sqlery):**

```python
from sqlery import job
from django.core.mail import send_mail

@job(queue='email', priority=10)
def send_welcome_email(user_id):
    user = User.objects.get(id=user_id)
    send_mail(
        'Welcome!',
        'Welcome to our app',
        'noreply@example.com',
        [user.email]
    )
    return f"Email sent to {user.email}"

# Usage (same API!)
send_welcome_email.delay(user_id=123)
# Or more explicit
send_welcome_email.enqueue(user_id=123)
```

### Example 2: Task with Retry

**Before (Celery):**

```python
from celery import shared_task
import requests

@shared_task(bind=True, max_retries=3)
def fetch_api_data(self, url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```

**After (sqlery):**

```python
from sqlery import job
import requests

@job(max_retries=3, retry_backoff=1.0, timeout_seconds=10)
def fetch_api_data(url):
    # No need for try/except - automatic retry on exception!
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

# Exponential backoff automatic: 1s, 2s, 4s, 8s
```

### Example 3: Long-Running Report Task

**Before (Celery):**

```python
from celery import shared_task
import time

@shared_task(time_limit=300, soft_time_limit=280)
def generate_monthly_report(month):
    # Generate report (takes ~5 minutes)
    data = collect_data(month)
    report = process_data(data)
    save_report(report)
    return f"Report for {month} completed"

# Usage
generate_monthly_report.apply_async(args=[month], queue='reports')
```

**After (sqlery):**

```python
from sqlery import job

@job(queue='reports', timeout_seconds=300, allow_parallel=False)
def generate_monthly_report(month):
    # Generate report (takes ~5 minutes)
    data = collect_data(month)
    report = process_data(data)
    save_report(report)
    return f"Report for {month} completed"

# Usage
generate_monthly_report.enqueue(month=month)
```

## Handling Unsupported Features

### Task Chains (NOT SUPPORTED)

**Celery:**

```python
from celery import chain

# Chain: download -> process -> upload
result = chain(
    download_file.s(url),
    process_file.s(),
    upload_file.s(destination)
).apply_async()
```

**sqlery workaround:**

```python
# Refactor into a single task that calls sub-functions
from sqlery import job

@job
def download_process_upload_pipeline(url, destination):
    # Step 1: Download
    file_data = download_file_logic(url)

    # Step 2: Process
    processed_data = process_file_logic(file_data)

    # Step 3: Upload
    upload_file_logic(processed_data, destination)

    return "Pipeline completed"

# Or use job dependencies (manual)
@job
def download_and_schedule_next(url):
    file_path = download_file_logic(url)

    # Enqueue next step
    process_and_schedule_next.enqueue(file_path=file_path)
    return file_path

@job
def process_and_schedule_next(file_path):
    processed = process_file_logic(file_path)

    # Enqueue final step
    upload_file.enqueue(data=processed)
    return processed
```

### Task Groups (NOT SUPPORTED)

**Celery:**

```python
from celery import group

# Run tasks in parallel
job = group(
    send_email.s('user1@example.com'),
    send_email.s('user2@example.com'),
    send_email.s('user3@example.com'),
)
result = job.apply_async()
```

**sqlery workaround:**

```python
# Enqueue multiple jobs (they run in parallel automatically)
from sqlery import enqueue

for email in ['user1@example.com', 'user2@example.com', 'user3@example.com']:
    send_email.enqueue(to=email, subject='Bulk email')

# With allow_parallel=True, they process concurrently
@job(queue='email', allow_parallel=True)
def send_email(to, subject):
    # ... send email
    pass
```

## Configuration Equivalents

| Celery Configuration | sqlery Equivalent |
|----------------------|---------------------------|
| `CELERY_BROKER_URL` | Not needed (uses database) |
| `CELERY_RESULT_BACKEND` | Not needed (database automatic) |
| `CELERY_TASK_ROUTES` | `DJANGO_SQL_JOBS['WORKER_QUEUES']` |
| `CELERY_TASK_TIME_LIMIT` | `@job(timeout_seconds=N)` |
| `CELERY_TASK_MAX_RETRIES` | `@job(max_retries=N)` |
| `CELERY_BEAT_SCHEDULE` | Django Admin → Scheduled Tasks |
| `task_serializer` | JSON (automatic) |
| `worker_concurrency` | `DJANGO_SQL_JOBS['MAX_WORKERS_PER_NODE']` |

## Gradual Migration Strategy

### Phase 1: Install Side-by-Side

1. Install sqlery alongside Celery
2. Keep Celery workers running
3. Convert one simple task as proof-of-concept

### Phase 2: Migrate Simple Tasks

1. Convert tasks without dependencies
2. Tasks without chains/groups/chords
3. Monitor for 1-2 weeks

### Phase 3: Migrate Scheduled Tasks

1. Disable Celery Beat tasks one by one
2. Recreate in sqlery
3. Verify execution

### Phase 4: Migrate Complex Tasks

1. Refactor chains into single tasks
2. Convert groups to multiple enqueues
3. Test thoroughly

### Phase 5: Decommission Celery

1. Stop Celery workers
2. Stop Celery Beat
3. Remove Celery dependencies
4. Remove broker (Redis/RabbitMQ) if not needed

## Performance Comparison

**Celery:**
- Extremely fast (broker optimized for messaging)
- Scales to millions of tasks
- Battle-tested at massive scale
- Complex setup and maintenance

**sqlery:**
- Fast enough for most Django apps (100s jobs/sec)
- Simpler architecture
- Scales to 100k-1M jobs/day
- Easy setup and maintenance

**Recommendation:** For typical Django applications with <100k jobs/day, sqlery provides simpler architecture with sufficient performance.

## Common Issues

### Issue: "Task chains not working"

**Solution:** Chains not supported. Refactor into single task or manual dependencies.

### Issue: "Need Flower dashboard"

**Solution:** Use built-in Django admin dashboard at `/admin/sqlery/dashboard/`

### Issue: "Canvas workflows required"

**Solution:** Canvas not supported. If you heavily use Canvas, stay with Celery.

### Issue: "Jobs slower than Celery"

**Solution:** This is expected - PostgreSQL slower than Redis. For >100k jobs/day, consider:
- Increasing `MAX_WORKERS_PER_NODE`
- Running multiple worker nodes
- Or staying with Celery

## Need Help?

- See [CONFIGURATION.md](CONFIGURATION.md) for settings reference
- See [MANAGEMENT_COMMANDS.md](MANAGEMENT_COMMANDS.md) for CLI commands
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues
- Open an issue on GitHub for migration-specific questions
