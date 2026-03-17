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

- Python 3.11 or higher
- PostgreSQL 12+ or SQLite 3.35+

### Install Sqlery

Choose your database backend:

**For SQLite** (easiest for getting started):
```bash
pip install sqlery aiosqlite
```

**For PostgreSQL** (async):
```bash
pip install sqlery asyncpg
```

**For PostgreSQL** (sync):
```bash
pip install sqlery psycopg2-binary
```

---

## Database Setup

### Create Database Tables

Sqlery needs two tables to operate:
- `sqlery_queued_job` - Stores jobs
- `sqlery_worker` - Tracks worker heartbeats
- `sqlery_scheduled_task` - Stores recurring tasks (optional)

**SQLite Setup**:
```python
import asyncio
from sqlery.backends import BackendFactory

async def setup_database():
    """Create database tables."""
    backend = BackendFactory.create_async_backend('sqlite:///jobs.db')
    await backend.connect()

    # Create tables
    await backend.db.execute("""
        CREATE TABLE IF NOT EXISTS sqlery_queued_job (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_path TEXT NOT NULL,
            kwargs TEXT DEFAULT '{}',
            queue_name TEXT DEFAULT 'default',
            priority INTEGER DEFAULT 0,
            status TEXT DEFAULT 'queued',
            scheduled_at TIMESTAMP,
            max_retries INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            retry_backoff REAL DEFAULT 1.0,
            allow_parallel BOOLEAN DEFAULT 0,
            timeout_seconds INTEGER,
            worker_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            duration_seconds REAL,
            output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            traceback TEXT DEFAULT '',
            termination_reason TEXT DEFAULT '',
            runs TEXT DEFAULT '[]',
            worker_pid INTEGER
        )
    """)

    await backend.db.execute("""
        CREATE TABLE IF NOT EXISTS sqlery_worker (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            pid INTEGER NOT NULL,
            status TEXT DEFAULT 'idle',
            current_job_id INTEGER,
            last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            jobs_processed INTEGER DEFAULT 0
        )
    """)

    await backend.db.execute("""
        CREATE TABLE IF NOT EXISTS sqlery_scheduled_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            task_path TEXT NOT NULL,
            cron_expression TEXT NOT NULL,
            queue_name TEXT DEFAULT 'default',
            priority INTEGER DEFAULT 0,
            enabled BOOLEAN DEFAULT 1,
            last_run_at TIMESTAMP,
            next_run_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await backend.disconnect()
    print("✓ Database tables created")

if __name__ == '__main__':
    asyncio.run(setup_database())
```

Save this as `setup_db.py` and run:
```bash
python setup_db.py
```

**PostgreSQL Setup**:

Use the same table definitions but with PostgreSQL-specific types:
- Change `INTEGER PRIMARY KEY AUTOINCREMENT` to `SERIAL PRIMARY KEY`
- Change `BOOLEAN` to use PostgreSQL's native boolean type

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

### Step 2: Configure the Backend

Create a file called `app.py`:

```python
# app.py
from sqlery import Queue
from sqlery.backends import BackendFactory
from tasks import send_welcome_email

# Configure backend
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()
Queue.configure(backend)

# Enqueue a job
job = send_welcome_email.delay('user@example.com', 'Alice')
# Or use RQ-style
# job = send_welcome_email.enqueue('user@example.com', 'Alice')

print(f"✓ Job {job['id']} enqueued")
print(f"  Queue: {job['queue_name']}")
print(f"  Status: {job['status']}")
```

Run it:
```bash
python app.py
```

You should see:
```
✓ Job 1 enqueued
  Queue: emails
  Status: queued
```

---

## Running the Worker

Now let's process the job with a worker.

### Step 3: Create a Worker Script

Create a file called `worker.py`:

```python
# worker.py
from sqlery import Worker
from sqlery.backends import BackendFactory
import tasks  # Import to register tasks

# Configure backend
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()

# Create worker for 'emails' queue
worker = Worker(['emails'], backend=backend)

print("Worker starting... Press Ctrl+C to stop")
worker.work()  # Runs forever
```

Run the worker in a separate terminal:
```bash
python worker.py
```

You should see:
```
Worker starting... Press Ctrl+C to stop
Worker worker-hostname-12345-1234567890 starting...
Worker worker-hostname-12345-1234567890 processing job 1
Executing tasks.send_welcome_email with args=('user@example.com', 'Alice'), kwargs={}
Sending welcome email to user@example.com
Welcome email sent to Alice!
Job 1 completed successfully
```

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
│  worker.py   │
│              │
│ Claims job,  │
│ executes,    │
│ updates DB   │
└──────────────┘
```

---

## Next Steps

Now that you have a basic job queue working, here are some next steps:

### 1. Try Async Workflows

```python
# async_tasks.py
from sqlery import async_job
import asyncio

@async_job(queue='reports')
async def generate_report(user_id):
    print(f"Generating report for user {user_id}")
    await asyncio.sleep(2)  # Simulate async work
    return f"Report for user {user_id} generated"

# async_worker.py
import asyncio
from sqlery import AsyncWorker
from sqlery.backends import BackendFactory
import async_tasks

async def main():
    backend = BackendFactory.create_async_backend('sqlite:///jobs.db')
    await backend.connect()

    worker = AsyncWorker(['reports'], backend=backend)
    await worker.work()

asyncio.run(main())
```

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
from sqlery import Queue

queue = Queue(name='maintenance', backend=backend)

# Run every day at 2 AM
queue.schedule(
    cron='0 2 * * *',
    func=cleanup_old_data,
    name='daily-cleanup'
)
```

### 5. Multiple Queues

```python
# Process multiple queues with different priorities
worker = Worker(
    queues=['high-priority', 'default', 'low-priority'],
    backend=backend
)
worker.work()
```

---

## Common Patterns

### Pattern 1: Separate App and Worker Processes

**Production setup**:
```
your-app/
├── app.py          # Web application (enqueues jobs)
├── tasks.py        # Task definitions
├── worker.py       # Background worker
└── requirements.txt
```

Run separately:
```bash
# Terminal 1: Run your app
python app.py

# Terminal 2: Run worker
python worker.py
```

### Pattern 2: Burst Mode (Process and Exit)

Useful for cron jobs or one-time processing:

```python
worker = Worker(['emails'], backend=backend, burst=True)
worker.work()  # Processes all jobs then exits
```

### Pattern 3: Multiple Workers

Scale horizontally by running multiple worker processes:

```bash
# Terminal 1
python worker.py

# Terminal 2
python worker.py

# Terminal 3
python worker.py
```

Workers automatically coordinate using database locks.

---

## Troubleshooting

### Jobs Not Processing

1. **Check worker is running**: Look for "Worker starting..." message
2. **Check queue names match**: Decorator `queue='emails'` must match `Worker(['emails'])`
3. **Check database connection**: Verify connection string is correct
4. **Check for errors**: Look in worker output for exceptions

### Import Errors

If you see `ImportError: Cannot import module tasks`:

1. **Ensure worker imports tasks**: Add `import tasks` at top of worker.py
2. **Check PYTHONPATH**: Worker needs to find your task modules
3. **Use absolute imports**: In tasks, use `from myproject.tasks import ...`

### Jobs Stuck in 'Running'

If jobs stay in 'running' status:

1. **Worker crashed**: Restart worker, it will claim stale jobs
2. **Job timeout**: Set reasonable `timeout` in decorator
3. **Check for infinite loops**: Ensure task functions complete

---

## Learn More

- [Configuration Guide](configuration.md) - All configuration options
- [API Reference](api-reference.md) - Complete API documentation
- [Examples](../examples/) - More example projects
- [Migration from Celery](migration-from-celery.md) - If coming from Celery
- [Migration from RQ](migration-from-rq.md) - If coming from RQ

---

**Need help?** [Open an issue](https://github.com/intrepid-g/sqlery/issues) or check the [documentation](https://sqlery.readthedocs.io).
