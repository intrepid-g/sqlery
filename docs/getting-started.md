# Getting Started with Sqlery

This guide will walk you through setting up sqlery and creating your first background job.

## Table of Contents

- [Installation](#installation)
- [Database Setup](#database-setup)
- [Your First Job](#your-first-job)
- [Running the Worker](#running-the-worker)
- [Understanding the Flow](#understanding-the-flow)
- [Next Steps](#next-steps)

---

## Installation

### Prerequisites

- Python 3.13 or higher
- PostgreSQL 12+ or SQLite 3.35+

### Install Sqlery

Standalone mode (no Django) needs the `standalone` extra:

```bash
pip install "sqlery[standalone]"
```

For PostgreSQL, add the `postgres` extra: `pip install "sqlery[standalone,postgres]"`.
SQLite needs nothing more.

---

## Database Setup

Sqlery creates its tables for you. Call `initialize()` once in each process,
before you enqueue jobs or start a worker:

```python
from sqlery.compat import initialize

initialize(database_url="sqlite:///jobs.db")
```

The database URL is `sqlite:///relative.db`, `sqlite:////absolute/path.db`, or
`postgresql://user:pass@host/dbname`.

The `sqlery-worker` and `sqlery jobs|tasks|queues` commands read the URL from
the `SQLERY_DATABASE_URL` (or `DATABASE_URL`) environment variable. They need no
`initialize()` call.

The removed `sqlery.backends.BackendFactory` and hand-written `CREATE TABLE`
statements are not needed any more.

---

## Your First Job

Let's create a simple background job that sends an email.

### Step 1: Define Your Task

Create a file called `tasks.py`:

```python
# tasks.py
from sqlery import job
import time

@job(queue='emails', timeout=60)
def send_welcome_email(email, username):
    """Send a welcome email to a new user."""
    print(f"Sending welcome email to {email}")
    time.sleep(2)  # Simulate email sending
    print(f"Welcome email sent to {username}!")
    return f"Email sent to {email}"
```

### Step 2: Enqueue a Job

Create a file called `app.py`:

```python
# app.py
from sqlery.compat import initialize
from tasks import send_welcome_email

initialize(database_url='sqlite:///jobs.db')

# Enqueue a job. .delay() and .enqueue() are the same call.
job = send_welcome_email.delay('user@example.com', 'Alice')
# job = send_welcome_email.enqueue('user@example.com', 'Alice')

# The result is the job row (an object, not a dict; the id is a large number).
print(f"Job {job.id} enqueued")
print(f"  Queue: {job.queue_name}")
print(f"  Status: {job.status}")
```

Run it:
```bash
python app.py
```

You should see:
```
Job 1684925837740022301 enqueued
  Queue: emails
  Status: queued
```

---

## Running the Worker

Now let's process the job with a worker. Use the `sqlery-worker` command.
The worker must be able to import your task module, so put the project folder on
`PYTHONPATH`:

```bash
export SQLERY_DATABASE_URL=sqlite:///jobs.db PYTHONPATH=.
sqlery-worker --queues emails
```

Run the worker in a separate terminal. It runs until you press Ctrl+C.
To stop after N jobs, add `--max-jobs N`.

The worker prints one line per job:

```
[CLAIMED] Job #1684925837740022301 (tasks.send_welcome_email)
[SUCCESS] Job #1684925837740022301
```

Check the result with `sqlery jobs list`. The job has status `success`.

---

## Understanding the Flow

Here's what just happened:

1. **Define Task** (`@job` decorator):
   ```python
   @job(queue='emails', timeout=60)
   def send_welcome_email(email, username):
       # Task logic
       pass
   ```

2. **Enqueue Job** (`.delay()` or `.enqueue()`):
   ```python
   job = send_welcome_email.delay('user@example.com', 'Alice')
   # Job is now stored in database with status='queued'
   ```

3. **Worker Claims Job**:
   - Worker polls database for jobs in 'emails' queue
   - Uses `SELECT FOR UPDATE SKIP LOCKED` to claim job atomically
   - Updates job status to 'running'

4. **Worker Executes Job**:
   - Imports task function dynamically
   - Executes with provided arguments
   - Captures return value and any errors

5. **Job Completes**:
   - Updates job status to 'success' (or 'failed')
   - Stores output/error in database
   - Worker moves to next job

### Architecture Diagram

```
┌──────────────┐
│   app.py     │
│              │
│ job = task.  │
│   delay()    │
└──────┬───────┘
       │ writes
       ▼
┌──────────────┐
│  Database    │
│ (jobs table) │
│              │
│ status=      │
│ 'queued'     │
└──────┬───────┘
       │ reads
       ▼
┌──────────────┐
│sqlery-worker │
│              │
│ Claims job,  │
│ executes,    │
│ updates DB   │
└──────────────┘
```

---

## Next Steps

Now that you have a basic job queue working, here are some next steps:

### 1. Use the Celery-style API

```python
from tasks import send_welcome_email

result = send_welcome_email.apply_async(args=('user@example.com', 'Alice'), countdown=30)
result.get(timeout=60)   # blocks until a worker finishes the job
```

See [CELERY_PARITY.md](CELERY_PARITY.md) for `AsyncResult`, `chain`, `group` and `revoke`.

### 2. Add Job Priority

```python
@job(queue='emails', priority=10)  # High priority
def send_urgent_email(email):
    pass

@job(queue='emails', priority=1)  # Low priority
def send_newsletter(email):
    pass
```

### 3. Configure Retries

```python
@job(max_retries=3, retry_backoff=2.0)
def flaky_api_call(url):
    # Will retry 3 times with exponential backoff
    # Retry delays: 1s, 2s, 4s
    pass
```

### 4. Schedule Recurring Jobs

```python
from sqlery.compat import initialize, get_backend

initialize(database_url='sqlite:///jobs.db')

# Run every day at 2 AM
get_backend().create_scheduled_task(
    name='daily-cleanup',
    task_path='tasks.cleanup_old_data',
    cron_expression='0 2 * * *',
    queue_name='default',
    priority=0,
)
```

### 5. Multiple Queues

```bash
# One worker, several queues. The first queue is checked first.
sqlery-worker --queues high-priority,default,low-priority
```

---

## Common Patterns

### Pattern 1: Separate App and Worker Processes

**Production setup**:
```
your-app/
├── app.py          # Web application (enqueues jobs)
├── tasks.py        # Task definitions
└── requirements.txt
```

Run separately:
```bash
# Terminal 1: Run your app
python app.py

# Terminal 2: Run worker
sqlery-worker --queues emails
```

### Pattern 2: Burst Mode (Process and Exit)

Useful for cron jobs or one-time processing:

```bash
sqlery-worker --queues emails --max-jobs 100   # exits after 100 jobs
```

In Django mode, use `python manage.py rqworker --burst emails`. It drains the queue and exits.

### Pattern 3: Multiple Workers

Scale horizontally by running multiple worker processes:

```bash
# Terminal 1, 2, 3 (same command in each)
sqlery-worker --queues emails
```

Workers automatically coordinate using database locks.

---

## Troubleshooting

### Jobs Not Processing

1. **Check worker is running**: Look for "Starting worker for queues: ..."
2. **Check queue names match**: Decorator `queue='emails'` must match `sqlery-worker --queues emails`
3. **Check database connection**: Verify connection string is correct
4. **Check for errors**: Look in worker output for exceptions

### Import Errors

If you see `ImportError: Cannot import module tasks`:

1. **Check PYTHONPATH**: The worker must be able to import your task module (`PYTHONPATH=.`)
2. **Check the task path**: The job stores a dotted path such as `tasks.send_welcome_email`
3. **Use absolute imports**: In tasks, use `from myproject.tasks import ...`

### Jobs Stuck in 'Running'

If jobs stay in 'running' status:

1. **Worker crashed**: Restart worker, it will claim stale jobs
2. **Job timeout**: Set reasonable `timeout` in decorator
3. **Check for infinite loops**: Ensure task functions complete

---

## Learn More

- [Configuration Guide](CONFIGURATION.md) - All configuration options
- [Examples](../examples/) - More example projects
- [Migration from Celery](MIGRATION_FROM_CELERY.md) - If coming from Celery
- [Migration from RQ](MIGRATION_FROM_RQ.md) - If coming from RQ

---

**Need help?** [Open an issue](https://github.com/intrepid-g/sqlery/issues) or check the [documentation](https://sqlery.readthedocs.io).
