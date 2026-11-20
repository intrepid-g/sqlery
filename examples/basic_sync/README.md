# Basic Sync Example

This example shows the basic workflow of sqlery in standalone mode, with SQLite.

## Files

- `setup_db.py` - Creates the database tables
- `tasks.py` - Defines the background jobs
- `enqueue.py` - Adds jobs to the queue
- `worker.py` - Runs jobs from the queue

## Quick Start

Run all commands from this folder.

### 1. Install

```bash
pip install "sqlery[standalone]"
# or, from a copy of the repository: pip install -e ".[standalone]"
```

### 2. Create the tables

```bash
python setup_db.py
```

### 3. Enqueue jobs

```bash
PYTHONPATH=. python enqueue.py
```

Output (job ids are different on each run):

```
1. Enqueueing email job...
   Job 3265212802174906814 enqueued (queue: default)
2. Enqueueing payment job (high priority)...
   Job 3114223427299146457 enqueued (priority: 10)
3. Enqueueing report job...
   Job 3993055259416165235 enqueued (queue: reports)
```

### 4. Run the worker

```bash
PYTHONPATH=. python worker.py
```

The worker stops after 3 jobs. Output:

```
[CLAIMED] Job #3114223427299146457 (tasks.process_payment)
[SUCCESS] Job #3114223427299146457
...
```

To run a worker with no limit, use the `sqlery-worker` command:

```bash
SQLERY_DATABASE_URL=sqlite:///example.db PYTHONPATH=. \
  sqlery-worker --queues default,reports
```

Add `--max-jobs N` to stop after N jobs. `PYTHONPATH=.` lets the worker import `tasks.py`.

## How It Works

### 1. Define tasks (tasks.py)

```python
from sqlery import job

@job(queue='default', timeout=60)
def send_email(to, subject, body):
    return f"Email sent to {to}"
```

The `@job` decorator makes the function a background task. Define tasks at module level.

### 2. Initialize sqlery (setup_db.py, enqueue.py, worker.py)

```python
from sqlery.compat import initialize

initialize(database_url="sqlite:///example.db", enable_daemon=False)
```

Call `initialize()` once in each process, before you enqueue jobs. It creates the tables if they do not exist.

### 3. Enqueue jobs (enqueue.py)

```python
job = send_email.delay('user@example.com', 'Hello', 'Welcome!')   # Celery style
job = send_email.enqueue('user@example.com', 'Hello', 'Welcome!') # RQ style
print(job.id)
```

Both calls return the job row. It is an object, not a dict: use `job.id`, `job.queue_name`, `job.priority`.

### 4. Process jobs (worker.py)

```python
from sqlery import Worker

worker = Worker(['default', 'reports'])
worker.run(max_jobs=3)   # no argument = run forever
```

The worker:
1. Polls the database for jobs
2. Claims each job so that no other worker runs it
3. Runs the task function
4. Saves the job status (success or failed)

## Next Steps

1. Add tasks in `tasks.py`.
2. Change the `priority` of a task.
3. Add retries with `max_retries` and `retry_backoff`.
4. Run `sqlery-worker` in many terminals.

## See Also

- [Async Example](../basic_async/) - Async worker
- [Getting Started Guide](../../docs/getting-started.md)
- [Configuration Guide](../../docs/configuration.md)
