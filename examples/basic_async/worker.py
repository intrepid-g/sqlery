"""Async worker process example.

Run it from this folder: python worker.py
It stops after 8 jobs.
"""

import asyncio

from sqlery.core.async_worker import AsyncWorker
from sqlery.fastapi_sqlery import database
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend
import tasks  # noqa: F401  (the worker must be able to import the tasks)

ASYNC_DATABASE_URL = "sqlite+aiosqlite:///example_async.db"


async def main():
    """Run an async worker that processes the 'default' and 'reports' queues."""
    database.get_async_engine(ASYNC_DATABASE_URL)
    worker = AsyncWorker(SQLAlchemyAsyncBackend(), ["default", "reports"], concurrency=5)
    print("Processing queues: default, reports (stops after 8 jobs)")
    await worker.run(max_jobs=8)


if __name__ == "__main__":
    asyncio.run(main())
