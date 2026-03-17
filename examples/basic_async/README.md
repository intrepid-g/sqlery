# Basic Async Example

This example demonstrates the asynchronous workflow with sqlery using `async`/`await`.

## Files

- `setup_db.py` - Creates database tables
- `tasks.py` - Defines async background jobs
- `enqueue.py` - Enqueues async jobs
- `worker.py` - Processes jobs asynchronously

## Quick Start

### 1. Install Dependencies

```bash
# From project root
pip install -e .
pip install aiosqlite aiohttp
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
Enqueueing Async Jobs
============================================================

1. Enqueueing data processing job...
   ✓ Job 1 enqueued (queue: default)

2. Enqueueing report generation job...
   ✓ Job 2 enqueued (queue: reports)

3. Enqueueing batch notifications job...
   ✓ Job 3 enqueued

4. Enqueueing multiple processing jobs...
   ✓ Job 4 enqueued (data_id=1000)
   ✓ Job 5 enqueued (data_id=1001)
   ✓ Job 6 enqueued (data_id=1002)
   ✓ Job 7 enqueued (data_id=1003)
   ✓ Job 8 enqueued (data_id=1004)

============================================================
Async Jobs Enqueued!
============================================================
```

### 4. Run Async Worker

In a separate terminal:

```bash
python worker.py
```

You should see the async worker process each job:
```
============================================================
Async Worker Starting
============================================================

Processing queues: default, reports
Press Ctrl+C to stop

Worker worker-hostname-12345-1234567890 starting...
Worker worker-hostname-12345-1234567890 processing job 1
Executing tasks.process_data_async with args=(), kwargs={'data_id': 12345, 'processing_time': 2}
⚙️  Processing data 12345
✓ Data 12345 processed
Job 1 completed successfully
...
```

## How It Works

### 1. Define Async Tasks (tasks.py)

```python
from sqlery import async_job
import asyncio

@async_job(queue='default', timeout=60)
async def process_data_async(data_id):
    print(f"Processing {data_id}")
    await asyncio.sleep(2)  # Async work
    return f"Processed {data_id}"
```

The `@async_job` decorator registers the async function as a background task.

### 2. Configure Backend (enqueue.py)

```python
import asyncio
from sqlery import AsyncQueue
from sqlery.backends import BackendFactory

async def main():
    backend = BackendFactory.create_async_backend('sqlite:///example_async.db')
    await backend.connect()
    AsyncQueue.configure(backend)

asyncio.run(main())
```

### 3. Enqueue Async Jobs (enqueue.py)

```python
from tasks import process_data_async

# Must await in async context
job = await process_data_async.delay(data_id=123)
# Or RQ-style
job = await process_data_async.enqueue(data_id=123)
```

### 4. Process Async Jobs (worker.py)

```python
import asyncio
from sqlery import AsyncWorker

async def main():
    worker = AsyncWorker(['default'], backend=backend)
    await worker.work()  # Async processing

asyncio.run(main())
```

The async worker:
1. Uses non-blocking database operations
2. Detects if tasks are async or sync
3. Runs async tasks with `await`
4. Runs sync tasks in executor (non-blocking)

## Advantages of Async

### Concurrent Execution

Async tasks can perform multiple operations concurrently:

```python
@async_job()
async def send_notifications_batch(user_ids):
    """Send to multiple users concurrently."""
    async def send_one(user_id):
        await send_notification(user_id)

    # All send concurrently!
    await asyncio.gather(*[send_one(uid) for uid in user_ids])
```

### Non-Blocking I/O

Perfect for I/O-bound tasks:

```python
@async_job()
async def fetch_and_process(urls):
    """Fetch multiple URLs concurrently."""
    async with aiohttp.ClientSession() as session:
        tasks = [session.get(url) for url in urls]
        responses = await asyncio.gather(*tasks)
        return [await r.text() for r in responses]
```

### Efficient Resource Usage

Async workers use less memory and can handle more concurrent jobs than sync workers with threads/processes.

## Sync vs Async Comparison

| Aspect | Sync | Async |
|--------|------|-------|
| Syntax | Regular functions | `async def`, `await` |
| Decorator | `@job` | `@async_job` |
| Queue | `Queue` | `AsyncQueue` |
| Worker | `Worker` | `AsyncWorker` |
| Enqueue | `task.delay()` | `await task.delay()` |
| Best For | CPU-bound, blocking I/O | I/O-bound, network calls |

## Features Demonstrated

- ✅ **Async job decoration** - Using `@async_job`
- ✅ **Async enqueueing** - `await task.delay()`
- ✅ **Async worker** - Non-blocking job processing
- ✅ **Concurrent operations** - Using `asyncio.gather()`
- ✅ **Mixed sync/async** - Worker handles both
- ✅ **Priority handling** - Async supports priorities too

## Common Patterns

### Pattern 1: Async HTTP Requests

```python
@async_job()
async def fetch_user_data(user_id):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'/api/users/{user_id}') as resp:
            return await resp.json()
```

### Pattern 2: Database Operations

```python
@async_job()
async def update_user_records(user_ids):
    async with aiopg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            for user_id in user_ids:
                await conn.execute('UPDATE users SET ...')
```

### Pattern 3: Batch Processing

```python
@async_job()
async def process_batch(items):
    # Process all items concurrently
    tasks = [process_item(item) for item in items]
    results = await asyncio.gather(*tasks)
    return results
```

## Next Steps

Try modifying the example:

1. **Add HTTP calls** - Use `aiohttp` for real API calls
2. **Database operations** - Use `asyncpg` or `aiosqlite`
3. **Concurrent batches** - Process multiple items at once
4. **Error handling** - Add try/except in async functions
5. **Mixed workers** - Run both sync and async workers

## See Also

- [Sync Example](../basic_sync/) - Synchronous workflow
- [Getting Started Guide](../../docs/getting-started.md) - Detailed walkthrough
- [Configuration Guide](../../docs/configuration.md) - All options
