"""Async worker process example."""

import asyncio
from sqlery import AsyncWorker
from sqlery.backends import BackendFactory
import tasks  # Import to register tasks


async def main():
    """Run an async worker to process jobs."""
    # Configure backend
    backend = BackendFactory.create_async_backend('sqlite:///example_async.db')
    await backend.connect()

    # Create async worker for multiple queues
    worker = AsyncWorker(['default', 'reports'], backend=backend)

    print("=" * 60)
    print("Async Worker Starting")
    print("=" * 60)
    print()
    print("Processing queues: default, reports")
    print("Press Ctrl+C to stop")
    print()

    # Start processing jobs (runs forever)
    await worker.work()


if __name__ == '__main__':
    asyncio.run(main())
