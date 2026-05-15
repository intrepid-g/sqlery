# Sqlery

**A lightweight, database-backed job queue for Python with Celery and RQ compatibility.**

Sqlery is a job queue system that uses PostgreSQL or SQLite as its backend, providing a simple, reliable alternative to Redis-based queues. Perfect for applications already using a SQL database.

## ✨ Key Features

- **🔄 Celery/RQ Compatible API** - Familiar `.delay()` and `.enqueue()` methods
- **⚡ Sync + Async Support** - Native support for both synchronous and asynchronous workflows
- **💾 SQL Backend** - Uses PostgreSQL or SQLite (no Redis required)
- **🎯 Type-Safe** - Full type hints throughout the codebase
- **🪶 Lightweight** - Minimal dependencies (`databases` + your DB driver)
- **📦 Self-Contained** - No external brokers or services needed
- **⏰ Cron Scheduling** - Built-in support for recurring tasks
- **🔁 Automatic Retries** - Configurable retry logic with exponential backoff

## 🚀 Quick Start

### Installation

```bash
# With PostgreSQL
pip install sqlery asyncpg

# With SQLite
pip install sqlery aiosqlite

# With sync PostgreSQL (psycopg2)
pip install sqlery psycopg2-binary
```

### Basic Usage (Sync)

```python
from sqlery import job, Queue, Worker
from sqlery.backends import BackendFactory

# 1. Configure backend
backend = BackendFactory.create_sync_backend('sqlite:///jobs.db')
backend.connect()
Queue.configure(backend)

# 2. Define jobs
@job(queue='default', timeout=300)
def send_email(to, subject, body):
    print(f"Sending email to {to}: {subject}")
    # Your email logic here
    return f"Email sent to {to}"

# 3. Enqueue jobs
job = send_email.delay('user@example.com', 'Hello', 'Welcome!')
# Or RQ-style
job = send_email.enqueue('user@example.com', 'Hello', 'Welcome!')

print(f"Job {job['id']} enqueued")

# 4. Run worker (in separate process)
worker = Worker(['default'], backend=backend)
worker.work()  # Runs forever, processing jobs
```

### Basic Usage (Async)

```python
import asyncio
from sqlery import async_job, AsyncQueue, AsyncWorker
from sqlery.backends import BackendFactory

async def main():
    # 1. Configure backend
    backend = BackendFactory.create_async_backend('sqlite:///jobs.db')
    await backend.connect()
    AsyncQueue.configure(backend)

    # 2. Define jobs
    @async_job(queue='default', timeout=300)
    async def process_data(data_id):
        print(f"Processing data {data_id}")
        await asyncio.sleep(1)  # Simulate async work
        return f"Data {data_id} processed"

    # 3. Enqueue jobs
    job = await process_data.delay(123)
    # Or RQ-style
    job = await process_data.enqueue(123)

    print(f"Job {job['id']} enqueued")

    # 4. Run worker
    worker = AsyncWorker(['default'], backend=backend)
    await worker.work()  # Runs forever, processing jobs

asyncio.run(main())
```

## 📖 Documentation

- [Getting Started Guide](docs/getting-started.md) - Detailed walkthrough
- [Configuration](docs/configuration.md) - All configuration options
- [API Reference](docs/api-reference.md) - Complete API documentation
- [Examples](examples/) - Working example projects
- [Migration from Celery](docs/migration-from-celery.md) - How to migrate
- [Migration from RQ](docs/migration-from-rq.md) - How to migrate

## 🔒 Security

See [Security Guide (docs/SECURITY.md)](docs/SECURITY.md) for the full security model: dashboard authentication (three modes), webhook SSRF protection, `ALLOWED_TASK_MODULES` allowlist, Django CSRF audit, and the project's dead-code retention policy.

## 🎯 Use Cases

Sqlery is ideal when you:

- ✅ Already use PostgreSQL or SQLite
- ✅ Want to avoid running Redis
- ✅ Need simple background job processing
- ✅ Want Celery/RQ-like API without complexity
- ✅ Need both sync and async support
- ✅ Want easy deployment (no extra services)

## 🔄 Comparison

| Feature | Sqlery | Celery | RQ |
|---------|--------|--------|-----|
| Backend | PostgreSQL, SQLite | Redis, RabbitMQ, etc. | Redis |
| Async Support | ✅ Native | ✅ Via asyncio | ❌ No |
| Setup Complexity | ⭐ Low | ⭐⭐⭐ High | ⭐⭐ Medium |
| Dependencies | Minimal | Many | Few |
| Type Hints | ✅ Full | ⚠️ Partial | ⚠️ Partial |
| Learning Curve | ⭐ Easy | ⭐⭐⭐ Steep | ⭐⭐ Moderate |

## 💡 Advanced Features

### Priority Queues

```python
@job(queue='emails', priority=10)  # Higher priority
def send_urgent_email(to, subject):
    pass

@job(queue='emails', priority=1)  # Lower priority
def send_newsletter(to):
    pass
```

### Retries with Backoff

```python
@job(max_retries=3, retry_backoff=2.0)
def flaky_api_call(url):
    # Will retry 3 times with exponential backoff
    pass
```

### Scheduled Jobs (Cron)

```python
from sqlery import Queue

queue = Queue(name='default', backend=backend)

# Run daily at 2 AM
queue.schedule(
    cron='0 2 * * *',
    func=cleanup_old_data,
    name='daily-cleanup'
)
```

### Multiple Queues

```python
# Process multiple queues with priority
worker = Worker(
    queues=['high-priority', 'default', 'low-priority'],
    backend=backend
)
worker.work()
```

## 🏗️ Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│             │         │              │         │             │
│  Your App   │────────▶│  PostgreSQL  │◀────────│   Worker    │
│             │ enqueue │   or SQLite  │  claim  │             │
│             │         │              │         │             │
└─────────────┘         └──────────────┘         └─────────────┘
                              │
                              │ stores
                              ▼
                        ┌──────────┐
                        │   Jobs   │
                        │  Tasks   │
                        │ Workers  │
                        └──────────┘
```

## 🛠️ Development

### Setup

```bash
# Clone repository
git clone https://github.com/intrepid-g/sqlery.git
cd sqlery

# Install with dev dependencies
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=sqlery
```

### Running Examples

```bash
# Basic sync example
cd examples/basic_sync
uv run python main.py

# Basic async example
cd examples/basic_async
uv run python main.py

# FastAPI integration
cd examples/fastapi_integration
uv run uvicorn app:app --reload
# In another terminal:
uv run python worker.py
```

## 📝 Requirements

- Python 3.11+
- PostgreSQL 12+ or SQLite 3.35+
- `databases` library
- Database driver:
  - `asyncpg` (async PostgreSQL)
  - `psycopg2` (sync PostgreSQL)
  - `aiosqlite` (async SQLite)

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Areas for Contribution

- 📚 Documentation improvements
- 🐛 Bug fixes
- ✨ New features (see [issues](https://github.com/intrepid-g/sqlery/issues))
- 🧪 More test coverage
- 📦 Example projects

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- Inspired by [Celery](https://github.com/celery/celery) and [RQ](https://github.com/rq/rq)
- Built with [databases](https://github.com/encode/databases)

## 🔗 Links

- [Documentation](https://sqlery.readthedocs.io)
- [PyPI Package](https://pypi.org/project/sqlery/)
- [GitHub Repository](https://github.com/intrepid-g/sqlery)
- [Issue Tracker](https://github.com/intrepid-g/sqlery/issues)
- [Changelog](CHANGELOG.md)

---

**Made with ❤️ by the sqlery team**
