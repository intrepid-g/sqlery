# Basic Async Example

This example shows the async worker of sqlery in standalone mode, with SQLite.

What is async here: the worker (`AsyncWorker`) and the task bodies (`async def`). The worker runs many jobs at the same time in one process. Enqueueing is a normal blocking call, because the standalone `@job` API has no `await`-able enqueue.

## Files

- `setup_db.py` - Creates the database tables
- `tasks.py` - Defines the async jobs
- `enqueue.py` - Adds jobs to the queue
- `worker.py` - Runs jobs with `AsyncWorker`

## Quick Start

Run all commands from this folder.

### 1. Install

```bash
pip install "sqlery[standalone]" aiosqlite
# or, from a copy of the repository: pip install -e ".[standalone]" aiosqlite
```

`aiosqlite` is the async SQLite driver. The `fetch_url` task also needs `aiohttp`, but the example does not enqueue it.

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
1. Enqueueing data processing job...
   Job 4191739753678047441 enqueued (queue: default)
...
```

### 4. Run the async worker

```bash
PYTHONPATH=. python worker.py
```

The worker stops after 8 jobs. It runs up to 5 jobs at the same time:

```
Processing queues: default, reports (stops after 8 jobs)
Processing data 12345
Processing data 1000
...
```

## How It Works

### 1. Define tasks (tasks.py)

```python
from sqlery import job
import asyncio

@job(queue='default', timeout=60)
async def process_data_async(data_id, processing_time=2):
    await asyncio.sleep(processing_time)
    return f"Data {data_id} processed"
```

Use the same `@job` decorator as in the sync example. The worker awaits `async def` tasks. It runs normal `def` tasks in a thread.

### 2. Enqueue jobs (enqueue.py)

```python
from sqlery.compat import initialize

initialize(database_url="sqlite:///example_async.db", enable_daemon=False)
job = process_data_async.delay(data_id=123)   # no await
print(job.id)
```

`.delay()` and `.enqueue()` return the job row. It is an object, not a dict.

### 3. Process jobs (worker.py)

```python
from sqlery.core.async_worker import AsyncWorker
from sqlery.fastapi_sqlery import database
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend

database.get_async_engine("sqlite+aiosqlite:///example_async.db")
worker = AsyncWorker(SQLAlchemyAsyncBackend(), ["default", "reports"], concurrency=5)
await worker.run(max_jobs=8)   # no argument = run forever
```

Use the `sqlite+aiosqlite://` URL here. The enqueue side uses the plain `sqlite://` URL for the same file.

## Sync vs Async

| Aspect | Sync | Async |
|--------|------|-------|
| Task | `def` | `async def` |
| Decorator | `@job` | `@job` |
| Enqueue | `task.delay()` | `task.delay()` |
| Worker | `Worker` (`sqlery-worker`) | `AsyncWorker` |
| Best for | CPU work, blocking I/O | Many waiting I/O jobs |

## See Also

- [Sync Example](../basic_sync/) - Sync worker
- [Getting Started Guide](../../docs/getting-started.md)
- [Configuration Guide](../../docs/configuration.md)
