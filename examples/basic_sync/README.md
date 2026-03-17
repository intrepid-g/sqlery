# Basic Sync Example

This example demonstrates the basic synchronous workflow with sqlery.

## Files

- `setup_db.py` - Creates database tables
- `tasks.py` - Defines background jobs
- `enqueue.py` - Enqueues jobs to be processed
- `worker.py` - Processes jobs from the queue

## Quick Start

### 1. Install Dependencies

```bash
# From project root
pip install -e .
pip install aiosqlite
```

### 2. Setup Database

```bash
python setup_db.py
```

### 3. Enqueue Jobs

```bash
python enqueue.py
```

You should see:
```
============================================================
Enqueueing Jobs
============================================================

1. Enqueueing email job...
   ✓ Job 1 enqueued (queue: default)

2. Enqueueing payment job (high priority)...
   ✓ Job 2 enqueued (priority: 10)

3. Enqueueing report job...
   ✓ Job 3 enqueued (queue: reports)

============================================================
Jobs Enqueued!
============================================================

Run 'python worker.py' to process these jobs
```

### 4. Run Worker

In a separate terminal:

```bash
python worker.py
```

You should see the worker process each job:
```
============================================================
Worker Starting
============================================================

Processing queues: default, reports
Press Ctrl+C to stop

Worker worker-hostname-12345-1234567890 starting...
Worker worker-hostname-12345-1234567890 processing job 2
Executing tasks.process_payment with args=(), kwargs={'user_id': 12345, 'amount': 99.99}
💳 Processing payment for user 12345
   Amount: $99.99
✓ Payment processed for user 12345
Job 2 completed successfully
...
```

## How It Works

### 1. Define Tasks (tasks.py)

```python
from sqlery import job

@job(queue='default', timeout=60)
def send_email(to, subject, body):
    # Your task logic here
    return f"Email sent to {to}"
```

The `@job` decorator registers the function as a background task.

### 2. Configure Backend (enqueue.py)

```python
from sqlery import Queue
from sqlery.backends import BackendFactory

backend = BackendFactory.create_sync_backend('sqlite:///example.db')
backend.connect()
Queue.configure(backend)
```

This sets up the SQLite database connection.

### 3. Enqueue Jobs (enqueue.py)

```python
from tasks import send_email

# Celery-style
job = send_email.delay('user@example.com', 'Hello', 'Welcome!')

# Or RQ-style
job = send_email.enqueue('user@example.com', 'Hello', 'Welcome!')
```

Jobs are stored in the database with status='queued'.

### 4. Process Jobs (worker.py)

```python
from sqlery import Worker

worker = Worker(['default', 'reports'], backend=backend)
worker.work()  # Runs forever, processing jobs
```

The worker:
1. Polls the database for jobs
2. Claims jobs atomically (preventing duplicates)
3. Executes the task function
4. Updates the job status (success/failed)

## Features Demonstrated

- ✅ **Job decoration** - Using `@job` decorator
- ✅ **Job enqueueing** - Both `.delay()` and `.enqueue()`
- ✅ **Multiple queues** - 'default' and 'reports'
- ✅ **Priority handling** - Payment jobs have priority=10
- ✅ **Worker processing** - Background job execution
- ✅ **Error handling** - Automatic capture of exceptions

## Next Steps

Try modifying the example:

1. **Add more tasks** - Define new functions in `tasks.py`
2. **Change priorities** - Adjust the `priority` parameter
3. **Add retries** - Use `max_retries` and `retry_backoff`
4. **Multiple workers** - Run `worker.py` in multiple terminals
5. **Scheduled jobs** - Use `queue.schedule()` for cron-style scheduling

## See Also

- [Async Example](../basic_async/) - Asynchronous workflow
- [Getting Started Guide](../../docs/getting-started.md) - Detailed walkthrough
- [Configuration Guide](../../docs/configuration.md) - All options
