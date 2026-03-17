# Migrating from RQ to sqlery

Complete guide for migrating from RQ (Redis Queue) to sqlery.

## Why Migrate?

**Benefits of switching to sqlery:**
- ✅ **No Redis dependency** - Use your existing PostgreSQL database
- ✅ **Integrated scheduling** - No need for rq-scheduler addon
- ✅ **Built-in admin UI** - No need for rq-dashboard addon
- ✅ **Serverless support** - Run on AWS Lambda, Cloud Run
- ✅ **Automatic cleanup** - Database retention policies built-in
- ✅ **Django-native** - Seamless Django integration

## Feature Mapping

| RQ Feature | sqlery Equivalent |
|------------|---------------------------|
| `Queue()` | `enqueue()` with `queue` parameter |
| `@job` decorator | `@job` decorator (same API!) |
| `queue.enqueue()` | `enqueue()` or `job_func.enqueue()` |
| `queue.enqueue_at()` | `enqueue_at()` or `job_func.enqueue_at()` |
| Job registries | Built-in RQ-compatible registries |
| `rq worker` | `python manage.py daemon start` |
| `rq-scheduler` | Built-in cron scheduling |
| `rq-dashboard` | Built-in Django admin dashboard |
| Job retry | Built-in with exponential backoff |
| Job TTL | Job retention policies |

## Migration Steps

### Step 1: Install sqlery

```bash
# Remove RQ dependencies
pip uninstall rq rq-scheduler rq-dashboard

# Install sqlery
pip install sqlery
```

### Step 2: Update Django Settings

**Remove RQ configuration:**

```python
# OLD - RQ configuration
RQ_QUEUES = {
    'default': {
        'HOST': 'localhost',
        'PORT': 6379,
        'DB': 0,
    },
    'high': {
        'HOST': 'localhost',
        'PORT': 6379,
        'DB': 0,
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

    # Multi-worker (equivalent to multiple RQ workers)
    'MAX_WORKERS_PER_NODE': 3,
    'WORKER_QUEUES': ['high', 'default', 'low'],
    'QUEUE_PRIORITIES': {
        'high': 100,
        'default': 50,
        'low': 10,
    },
}
```

### Step 3: Update Task Definitions

**RQ tasks:**

```python
# OLD - RQ
from redis import Redis
from rq import Queue
from rq.decorators import job

redis_conn = Redis()
queue = Queue(connection=redis_conn)

# Basic task
@job('default', connection=redis_conn)
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

# Task with retry
@job('default', connection=redis_conn, retry=Retry(max=3))
def unreliable_task():
    # ... task logic
    pass
```

**sqlery tasks:**

```python
# NEW - sqlery
from sqlery import job

# Basic task (same decorator name!)
@job
def send_email(to, subject):
    # ... send email
    return f"Sent to {to}"

# Task with retry
@job(max_retries=3, retry_backoff=1.0)
def unreliable_task():
    # ... task logic
    pass

# Task with queue and priority
@job(queue='high', priority=100)
def urgent_task():
    # ... task logic
    pass
```

### Step 4: Update Task Enqueuing

**RQ enqueuing:**

```python
# OLD - RQ
from redis import Redis
from rq import Queue

redis_conn = Redis()
queue = Queue('default', connection=redis_conn)

# Enqueue immediately
job = queue.enqueue(send_email, to='user@example.com', subject='Hello')

# Enqueue with delay
from datetime import timedelta
job = queue.enqueue_in(timedelta(hours=1), send_email,
                        to='user@example.com', subject='Hello')

# Enqueue at specific time
from datetime import datetime
run_at = datetime(2025, 10, 20, 10, 0)
job = queue.enqueue_at(run_at, send_email,
                       to='user@example.com', subject='Hello')
```

**sqlery enqueuing:**

```python
# NEW - sqlery

# Option 1: Using decorator methods (cleanest)
job = send_email.enqueue(to='user@example.com', subject='Hello')

# Option 2: Using enqueue function
from sqlery import enqueue
job = enqueue('myapp.tasks.send_email',
              to='user@example.com', subject='Hello',
              queue='default', priority=0)

# Enqueue with delay
from datetime import datetime, timezone, timedelta
run_at = datetime.now(timezone.utc) + timedelta(hours=1)
job = send_email.enqueue_at(run_at, to='user@example.com', subject='Hello')

# Enqueue at specific time
from datetime import datetime, timezone
run_at = datetime(2025, 10, 20, 10, 0, tzinfo=timezone.utc)
job = send_email.enqueue_at(run_at, to='user@example.com', subject='Hello')
```

### Step 5: Update Worker Management

**RQ workers:**

```bash
# OLD - RQ workers
rq worker default high low

# Multiple workers
rq worker default &
rq worker default &
rq worker high &
```

**sqlery workers:**

```bash
# NEW - Start daemon (manages worker pool automatically)
python manage.py daemon start

# Workers are managed automatically based on MAX_WORKERS_PER_NODE
# No need to manually start multiple processes

# Check worker status
python manage.py workers list

# Stop all workers
python manage.py workers stop
```

### Step 6: Migrate Scheduled Jobs (from rq-scheduler)

**RQ scheduler:**

```python
# OLD - rq-scheduler
from redis import Redis
from rq_scheduler import Scheduler

scheduler = Scheduler(connection=Redis())

# Schedule job to run every hour
scheduler.cron(
    "0 * * * *",
    func=send_daily_report,
    queue_name='default'
)
```

**sqlery scheduler:**

Create via Django Admin → Scheduled Tasks, or programmatically:

```python
# NEW - sqlery
from sqlery.models import ScheduledTask

ScheduledTask.objects.create(
    name='Daily Report',
    task_path='myapp.tasks.send_daily_report',
    cron_expression='0 * * * *',  # Same cron syntax!
    queue_name='default',
    enabled=True
)
```

### Step 7: Migrate Job Registries

**RQ registries:**

```python
# OLD - RQ registries
from redis import Redis
from rq.registry import StartedJobRegistry, FinishedJobRegistry

redis = Redis()
started_registry = StartedJobRegistry('default', connection=redis)
finished_registry = FinishedJobRegistry('default', connection=redis)

# Get job IDs
started_jobs = started_registry.get_job_ids()
finished_jobs = finished_registry.get_job_ids()
```

**sqlery registries:**

```python
# NEW - sqlery (RQ-compatible API)
from sqlery.models import JobRegistry

# Get started jobs
started_jobs = JobRegistry.objects.filter(
    registry_type='started',
    queue_name='default'
)

# Get finished jobs
finished_jobs = JobRegistry.objects.filter(
    registry_type='finished',
    queue_name='default'
)

# Or use ORM queries
from sqlery.models import QueuedJob

running_jobs = QueuedJob.objects.filter(status='running')
completed_jobs = QueuedJob.objects.filter(status='success')
```

## Code Comparison Examples

### Example 1: Simple Email Task

**Before (RQ):**

```python
from redis import Redis
from rq import Queue
from rq.decorators import job

redis_conn = Redis()

@job('email', connection=redis_conn)
def send_welcome_email(user_id):
    user = User.objects.get(id=user_id)
    send_email(user.email, 'Welcome!', 'Welcome to our app')
    return f"Email sent to {user.email}"

# Enqueue
queue = Queue('email', connection=redis_conn)
queue.enqueue(send_welcome_email, user_id=123)
```

**After (sqlery):**

```python
from sqlery import job

@job(queue='email', priority=10)
def send_welcome_email(user_id):
    user = User.objects.get(id=user_id)
    send_email(user.email, 'Welcome!', 'Welcome to our app')
    return f"Email sent to {user.email}"

# Enqueue
send_welcome_email.enqueue(user_id=123)
```

### Example 2: Scheduled Task

**Before (RQ + rq-scheduler):**

```python
from redis import Redis
from rq_scheduler import Scheduler

def daily_cleanup():
    # Cleanup logic
    pass

scheduler = Scheduler(connection=Redis())
scheduler.cron("0 2 * * *", func=daily_cleanup, queue_name='maintenance')
```

**After (sqlery):**

```python
from sqlery import job

@job(queue='maintenance')
def daily_cleanup():
    # Cleanup logic
    pass

# Create scheduled task via Django Admin or:
from sqlery.models import ScheduledTask
ScheduledTask.objects.create(
    name='Daily Cleanup',
    task_path='myapp.tasks.daily_cleanup',
    cron_expression='0 2 * * *',
    queue_name='maintenance',
    enabled=True
)
```

### Example 3: Task with Retry

**Before (RQ):**

```python
from redis import Redis
from rq import Queue, Retry

redis_conn = Redis()
queue = Queue(connection=redis_conn)

def api_call():
    response = requests.get('https://api.example.com/data')
    response.raise_for_status()
    return response.json()

# Enqueue with retry
queue.enqueue(api_call, retry=Retry(max=3, interval=[1, 2, 4]))
```

**After (sqlery):**

```python
from sqlery import job

@job(max_retries=3, retry_backoff=1.0)
def api_call():
    response = requests.get('https://api.example.com/data')
    response.raise_for_status()
    return response.json()

# Enqueue (retry config from decorator)
api_call.enqueue()

# Or override per-call
api_call.enqueue(max_retries=5, retry_backoff=2.0)
```

## Gradual Migration Strategy

Don't want to migrate everything at once? Run both systems in parallel:

### Phase 1: Install and Test

1. Install sqlery alongside RQ
2. Convert one low-priority queue to sqlery
3. Test thoroughly in staging

### Phase 2: Migrate Scheduled Jobs

1. Disable rq-scheduler tasks
2. Recreate them in sqlery
3. Monitor for 1-2 days

### Phase 3: Migrate High-Volume Queues

1. Convert task definitions to use `@job` decorator
2. Update enqueue calls
3. Switch workers queue by queue

### Phase 4: Decommission RQ

1. Stop RQ workers
2. Remove RQ dependencies
3. Remove Redis connection if not used elsewhere

## Configuration Equivalents

| RQ Configuration | sqlery Equivalent |
|------------------|---------------------------|
| `RQ_QUEUES` dict | `DJANGO_SQL_JOBS['WORKER_QUEUES']` |
| `rq worker` count | `DJANGO_SQL_JOBS['MAX_WORKERS_PER_NODE']` |
| `result_ttl` | `DJANGO_SQL_JOBS['JOB_RETENTION']` |
| `failure_ttl` | `DJANGO_SQL_JOBS['JOB_RETENTION']['failed_max_age_days']` |
| `job_timeout` | `@job(timeout_seconds=N)` |
| `Retry(max=3)` | `@job(max_retries=3)` |

## Common Issues

### Issue: "Cannot connect to Redis"

**Solution:** You've successfully removed the Redis dependency! Remove any remaining Redis connection code.

### Issue: "Queue not found"

**Solution:** Ensure queue is listed in `WORKER_QUEUES`:

```python
DJANGO_SQL_JOBS = {
    'WORKER_QUEUES': ['high', 'default', 'low', 'your-queue-name'],
}
```

### Issue: "Jobs not processing"

**Solution:** Start the daemon:

```bash
python manage.py daemon start
python manage.py daemon status  # Verify running
```

## Performance Comparison

**RQ (Redis):**
- Very fast job claiming (~1000s jobs/second)
- Memory-based storage
- Requires separate Redis instance
- Network latency to Redis

**sqlery (PostgreSQL):**
- Fast job claiming (~100s jobs/second with SKIP LOCKED)
- Disk-based storage (persistent)
- Uses existing database
- No additional network hops

**Recommendation:** For most Django apps, PostgreSQL performance is sufficient. If you need >1000 jobs/second, consider multiple worker nodes or staying with RQ.

## Need Help?

- See [CONFIGURATION.md](CONFIGURATION.md) for settings reference
- See [MANAGEMENT_COMMANDS.md](MANAGEMENT_COMMANDS.md) for CLI commands
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues
- Open an issue on GitHub for migration-specific questions
