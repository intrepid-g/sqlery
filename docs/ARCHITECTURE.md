# Architecture Design: Dual-Mode Job System

## Two Complementary Systems

### 1. Scheduled Tasks (Cron)
- **Purpose**: Define WHEN to run jobs
- **Model**: `ScheduledTask`
- **Trigger**: Time-based (cron expressions)
- **Action**: Enqueues `QueuedJob` when due

### 2. Job Queue
- **Purpose**: Define WHAT to execute
- **Model**: `QueuedJob`
- **Trigger**: Manual enqueue OR scheduled task
- **Action**: Worker executes queued jobs

## Model Design

```python
class ScheduledTask(models.Model):
    """Defines recurring jobs via cron expressions."""
    name
    cron_expression      # When to run
    task_path           # What to run
    queue_name          # Which queue to enqueue to
    priority            # Priority for enqueued jobs
    enabled

    last_run_at
    next_run_at

class QueuedJob(models.Model):
    """A job in the queue, waiting to be executed."""
    task_path           # What to run
    queue_name          # Which queue (for filtering)
    priority            # Higher = sooner

    status              # queued → running → success/failed

    # Optional reference to scheduler
    scheduled_task      # NULL if manually enqueued

    # Execution tracking
    created_at          # When enqueued
    started_at          # When execution began
    finished_at         # When execution completed
    duration_seconds

    # Results
    output
    error
    traceback
```

## Flow Diagrams

### Cron-Triggered Jobs

```
ScheduledTask (cron: "0 2 * * *")
    ↓ (2 AM)
Scheduler checks next_run_at
    ↓
Create QueuedJob(status='queued', queue_name='default')
    ↓
Update ScheduledTask.next_run_at
    ↓
Worker finds QueuedJob with status='queued'
    ↓
Update to status='running'
    ↓
Execute task
    ↓
Update to status='success' or 'failed'
```

### Manual Jobs

```python
from sqlery import enqueue

# Enqueue immediately
job = enqueue('myapp.tasks.send_email', queue='email', priority=10)

# Job goes into queue
# Worker picks it up and executes
```

## Queue Processing

### Queue Selection

Workers can process specific queues:

```bash
# Process all queues
python manage.py run_queue_workers

# Process specific queue
python manage.py run_queue_workers --queue email

# Process multiple queues
python manage.py run_queue_workers --queue default --queue email
```

### Priority

Jobs with higher priority execute first:

```python
QueuedJob.objects.filter(
    status='queued',
    queue_name='default'
).order_by('-priority', 'created_at')
```

## Concurrency Control

Same as before - check for running jobs:

```python
if QueuedJob.objects.filter(
    task_path=job.task_path,
    status='running'
).exists():
    skip  # Already running
```

## Use Cases

### Use Case 1: Scheduled Report
```python
# In Admin: Create ScheduledTask
name: "Daily Report"
cron: "0 8 * * *"
task_path: "reports.tasks.generate_daily_report"
queue_name: "reports"
priority: 5

# What happens:
# - Every day at 8 AM, QueuedJob created
# - Worker processes it from "reports" queue
# - History tracked in QueuedJob records
```

### Use Case 2: Manual Job
```python
# In your view/API
from sqlery import enqueue

def send_welcome_email(request):
    job = enqueue(
        'emails.tasks.send_welcome',
        queue='email',
        priority=10
    )
    return Response({'job_id': job.id})
```

### Use Case 3: Serverless

> ⚠️ **Experimental:** The Lambda/serverless mode has only been smoke-tested
> (no LocalStack/SAM fidelity testing) and is **not** production-ready.

```bash
# AWS Lambda triggered every minute by EventBridge
python manage.py run_queue_workers --once

# Processes all queued jobs, then exits
# No always-running process
```

## Settings

```python
DJANGO_SQL_JOBS = {
    # Scheduler settings
    'ENABLE_MIDDLEWARE_TRIGGER': True,
    'CHECK_INTERVAL_SECONDS': 60,

    # Queue settings
    'DEFAULT_QUEUE': 'default',
    'DEFAULT_PRIORITY': 0,
    'USE_DJANGO_TASKS': True,

    # Worker settings
    'MAX_JOBS_PER_RUN': 100,  # For --once mode
}
```

## Key Differences from MVP

| Aspect | MVP | New Design |
|--------|-----|------------|
| **Job Creation** | Automatic (cron only) | Cron OR manual |
| **Job States** | running → success/failed | queued → running → success/failed |
| **Queue Names** | No | Yes (routing) |
| **Priority** | No | Yes |
| **Manual Enqueue** | No | Yes |
| **Model** | TaskExecution | QueuedJob |

## Migration Path

1. Rename `TaskExecution` → `QueuedJob`
2. Add fields: `queue_name`, `priority`, `scheduled_task`
3. Change default status from 'running' → 'queued'
4. Add `enqueue()` function
5. Update executor to process queue
6. Update admin

This maintains backward compatibility - existing cron tasks still work, just now they enqueue jobs first.
