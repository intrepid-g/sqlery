# Configuration Guide

This guide covers all configuration options for sqlery.

## Table of Contents

- [Backend Configuration](#backend-configuration)
- [Queue Configuration](#queue-configuration)
- [Worker Configuration](#worker-configuration)
- [Job Options](#job-options)
- [Security](#security)
- [Performance Tuning](#performance-tuning)

---

## Backend Configuration

### Connection Strings

Sqlery supports PostgreSQL and SQLite via connection strings.

#### SQLite

```python
from sqlery.backends import BackendFactory

# In-memory (testing only)
backend = BackendFactory.create_sync_backend('sqlite:///:memory:')

# File-based
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')

# Absolute path
backend = BackendFactory.create_sync_backend('sqlite:////absolute/path/to/jobs.db')
```

#### PostgreSQL

```python
# Basic connection
backend = BackendFactory.create_sync_backend('postgresql://user:pass@localhost/dbname')

# With port
backend = BackendFactory.create_sync_backend('postgresql://user:pass@localhost:5432/dbname')

# With additional options
backend = BackendFactory.create_sync_backend(
    'postgresql://user:pass@localhost/dbname?sslmode=require&connect_timeout=10'
)
```

### Sync vs Async Backends

**Sync Backend** (blocking):
```python
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()  # Blocking
# Use with Queue and Worker
backend.disconnect()
```

**Async Backend** (non-blocking):
```python
backend = BackendFactory.create_async_backend('sqlite:///jobs.db')
await backend.connect()  # Non-blocking
# Use with AsyncQueue and AsyncWorker
await backend.disconnect()
```

### Database Initialization

**Using schema module** (recommended):
```python
from sqlery import create_tables_sync, create_tables_async
from sqlery.backends import BackendFactory

# Sync
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()
create_tables_sync(backend)
backend.disconnect()

# Async
import asyncio

async def setup():
    backend = BackendFactory.create_async_backend('sqlite:///jobs.db')
    await backend.connect()
    await create_tables_async(backend)
    await backend.disconnect()

asyncio.run(setup())
```

**Manual SQL** (if needed):
```python
# See src/sqlery/schema.py for complete SQL definitions
await backend.db.execute("""
    CREATE TABLE IF NOT EXISTS sqlery_queued_job (
        id SERIAL PRIMARY KEY,
        task_path TEXT NOT NULL,
        ...
    )
""")
```

---

## Queue Configuration

### Default Backend

Configure a default backend for all queues:

```python
from sqlery import Queue
from sqlery.backends import BackendFactory

backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()

# Set default
Queue.configure(backend)

# Now all queues use this backend
queue1 = Queue(name='emails')
queue2 = Queue(name='reports')
```

### Explicit Backend

Pass backend explicitly:

```python
queue = Queue(name='emails', backend=backend)
```

### Queue Naming

Choose descriptive queue names:

```python
Queue(name='emails')        # Email sending tasks
Queue(name='reports')       # Report generation
Queue(name='high-priority') # Urgent tasks
Queue(name='low-priority')  # Background cleanup
```

### Default Timeout

Set default timeout for all jobs in a queue:

```python
queue = Queue(
    name='emails',
    backend=backend,
    default_timeout=300  # 5 minutes
)
```

---

## Worker Configuration

### Basic Worker

```python
from sqlery import Worker

worker = Worker(
    queues=['default'],  # Queue names to process
    backend=backend
)
worker.work()  # Runs forever
```

### Multiple Queues

Workers can process multiple queues:

```python
worker = Worker(
    queues=['high-priority', 'default', 'low-priority'],
    backend=backend
)
```

**Order matters**: Worker processes queues in order, giving priority to earlier queues.

### Burst Mode

Process all available jobs then exit:

```python
worker = Worker(
    queues=['default'],
    backend=backend,
    burst=True  # Exit when queue is empty
)
worker.work()
```

**Use cases**:
- Cron jobs
- One-time processing
- Testing

### Poll Interval

Control how often worker checks for new jobs:

```python
worker = Worker(
    queues=['default'],
    backend=backend,
    poll_interval=5.0  # Check every 5 seconds (default: 1.0)
)
```

**Trade-offs**:
- Lower = more responsive, more database queries
- Higher = less responsive, fewer database queries

### Worker ID

Customize worker identification:

```python
worker = Worker(
    queues=['default'],
    backend=backend,
    worker_id='worker-prod-1'  # Custom ID
)
```

**Default**: Auto-generated from hostname, PID, and timestamp.

### Async Worker

Same options, async context:

```python
import asyncio
from sqlery import AsyncWorker

async def main():
    worker = AsyncWorker(
        queues=['default'],
        backend=backend,
        burst=False,
        poll_interval=1.0
    )
    await worker.work()

asyncio.run(main())
```

---

## Job Options

### Priority

Higher priority jobs execute first:

```python
@job(priority=10)  # High priority
def urgent_task():
    pass

@job(priority=0)  # Default priority
def normal_task():
    pass

@job(priority=-5)  # Low priority
def cleanup_task():
    pass
```

**Range**: Any integer (higher = more urgent)

### Timeout

Maximum execution time:

```python
@job(timeout=300)  # 5 minutes
def long_running_task():
    pass

@job(timeout=30)  # 30 seconds
def quick_task():
    pass
```

**Units**: Seconds

**Behavior**: Worker terminates job after timeout.

### Retries

Automatic retry on failure:

```python
@job(max_retries=3, retry_backoff=2.0)
def flaky_task():
    # Will retry 3 times
    # Delays: 1s, 2s, 4s (exponential backoff)
    pass
```

**Parameters**:
- `max_retries`: Number of retry attempts
- `retry_backoff`: Backoff multiplier (default: 1.0)

**Backoff calculation**: `delay = retry_backoff ^ retry_count`

### Allow Parallel

Control parallel execution within same queue:

```python
@job(allow_parallel=False)
def exclusive_task():
    # Only one instance runs at a time
    pass

@job(allow_parallel=True)  # Default
def parallel_task():
    # Multiple instances can run
    pass
```

### Queue Routing

Route job to specific queue:

```python
@job(queue='emails')
def send_email():
    pass

@job(queue='reports')
def generate_report():
    pass
```

### Combined Options

All options together:

```python
@job(
    queue='high-priority',
    priority=100,
    timeout=600,
    max_retries=3,
    retry_backoff=2.0,
    allow_parallel=False
)
def critical_task():
    pass
```

---

## Security

### Connection String Secrets

**Never hardcode credentials**:

```python
# Bad
backend = BackendFactory.create_sync_backend('postgresql://admin:secret123@localhost/db')

# Good - Use environment variables
import os

DATABASE_URL = os.environ['DATABASE_URL']
backend = BackendFactory.create_sync_backend(DATABASE_URL)
```

### Environment Variables

Create `.env` file:

```env
DATABASE_URL=postgresql://user:pass@localhost/dbname
SQLERY_POLL_INTERVAL=2.0
SQLERY_DEFAULT_TIMEOUT=300
```

Load in code:

```python
from dotenv import load_dotenv
import os

load_dotenv()

backend = BackendFactory.create_sync_backend(os.environ['DATABASE_URL'])
```

### Database Permissions

**Minimum required permissions**:
- `SELECT` on all sqlery tables
- `INSERT` on `sqlery_queued_job`, `sqlery_scheduled_task`
- `UPDATE` on `sqlery_queued_job`, `sqlery_worker`
- `DELETE` on `sqlery_queued_job` (for cleanup)

**Example PostgreSQL**:
```sql
CREATE USER sqlery_worker WITH PASSWORD 'secure_password';
GRANT SELECT, INSERT, UPDATE, DELETE ON sqlery_queued_job TO sqlery_worker;
GRANT SELECT, INSERT, UPDATE ON sqlery_worker TO sqlery_worker;
GRANT SELECT ON sqlery_scheduled_task TO sqlery_worker;
```

---

## Performance Tuning

### Connection Pooling

The `databases` library handles connection pooling automatically.

**Tune pool size** (if needed):
```python
# Currently not exposed in sqlery, but databases uses defaults:
# - min_size: 1
# - max_size: 10
```

### Database Indexes

For better performance, add indexes:

```sql
-- Index on status for faster job claiming
CREATE INDEX idx_queued_job_status ON sqlery_queued_job(status);

-- Index on queue_name and priority for faster claiming
CREATE INDEX idx_queued_job_queue_priority
    ON sqlery_queued_job(queue_name, priority DESC, id);

-- Index on scheduled_at for scheduled jobs
CREATE INDEX idx_queued_job_scheduled
    ON sqlery_queued_job(scheduled_at)
    WHERE scheduled_at IS NOT NULL;
```

### Worker Scaling

**Horizontal scaling**: Run multiple worker processes

```bash
# Terminal 1
python worker.py

# Terminal 2
python worker.py

# Terminal 3
python worker.py
```

Workers automatically coordinate using database locks (`SELECT FOR UPDATE SKIP LOCKED`).

**Vertical scaling**: Adjust poll interval

```python
# More responsive but more DB queries
worker = Worker(queues=['default'], poll_interval=0.5)

# Less responsive but fewer DB queries
worker = Worker(queues=['default'], poll_interval=5.0)
```

### Queue Separation

Separate queues by workload type:

```python
# Fast tasks
fast_worker = Worker(queues=['fast'], backend=backend)

# Slow tasks
slow_worker = Worker(queues=['slow'], backend=backend)

# Separate processes or machines
```

### Batch Enqueueing

Enqueue multiple jobs efficiently:

```python
from sqlery import Queue

queue = Queue(name='default', backend=backend)

# Enqueue many jobs
for user_id in range(1000):
    send_email.delay(user_id=user_id)
```

For truly massive enqueueing, consider batching SQL inserts (advanced).

---

## Example Configurations

### Development

```python
# Simple SQLite, single worker
from sqlery import Queue, Worker
from sqlery.backends import BackendFactory

backend = BackendFactory.create_sync_backend('sqlite:///dev.db')
backend.connect()
Queue.configure(backend)

worker = Worker(['default'], backend=backend, poll_interval=1.0)
worker.work()
```

### Production

```python
# PostgreSQL with environment variables
import os
from sqlery import Queue, Worker
from sqlery.backends import BackendFactory

DATABASE_URL = os.environ['DATABASE_URL']
backend = BackendFactory.create_sync_backend(DATABASE_URL)
backend.connect()
Queue.configure(backend)

# Multiple queues with priorities
worker = Worker(
    queues=['critical', 'high', 'default', 'low'],
    backend=backend,
    poll_interval=2.0,
    worker_id=f"worker-{os.getpid()}"
)
worker.work()
```

### Testing

```python
# In-memory SQLite, burst mode
from sqlery import Queue, Worker
from sqlery.backends import BackendFactory

backend = BackendFactory.create_sync_backend('sqlite:///:memory:')
backend.connect()
Queue.configure(backend)

# Process all jobs then exit
worker = Worker(['default'], backend=backend, burst=True)
worker.work()
```

---

## Troubleshooting

### Jobs Not Processing

**Check**:
1. Worker is running
2. Queue names match
3. Backend is configured
4. Database tables exist

**Debug**:
```python
# Check job count
queue = Queue(name='default', backend=backend)
print(f"Queued jobs: {queue.count(status='queued')}")

# Check stats
stats = queue.get_stats()
print(f"Stats: {stats}")
```

### High Database Load

**Solutions**:
- Increase `poll_interval`
- Add database indexes
- Run fewer workers
- Separate queues by workload

### Memory Issues

**Solutions**:
- Use burst mode for worker restarts
- Monitor task memory usage
- Set reasonable timeouts
- Clean up old jobs regularly

---

## See Also

- [Getting Started Guide](getting-started.md) - Basic setup
- [API Reference](api-reference.md) - Complete API docs
- [Examples](../examples/) - Working examples
