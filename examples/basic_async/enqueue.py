"""Enqueue async jobs example."""

import asyncio
from sqlery import AsyncQueue
from sqlery.backends import BackendFactory
from tasks import process_data_async, generate_async_report, send_notifications_batch


async def main():
    """Enqueue several async jobs."""
    # Configure backend
    backend = BackendFactory.create_async_backend('sqlite:///example_async.db')
    await backend.connect()
    AsyncQueue.configure(backend)

    print("=" * 60)
    print("Enqueueing Async Jobs")
    print("=" * 60)
    print()

    # Enqueue some jobs
    print("1. Enqueueing data processing job...")
    job1 = await process_data_async.delay(data_id=12345, processing_time=2)
    print(f"   ✓ Job {job1['id']} enqueued (queue: {job1['queue_name']})")
    print()

    print("2. Enqueueing report generation job...")
    job2 = await generate_async_report.enqueue(
        report_type='analytics',
        user_id=67890
    )
    print(f"   ✓ Job {job2['id']} enqueued (queue: {job2['queue_name']})")
    print()

    print("3. Enqueueing batch notifications job...")
    job3 = await send_notifications_batch.delay(user_ids=[1, 2, 3, 4, 5])
    print(f"   ✓ Job {job3['id']} enqueued")
    print()

    print("4. Enqueueing multiple processing jobs...")
    for i in range(5):
        job = await process_data_async.delay(data_id=1000 + i, processing_time=1)
        print(f"   ✓ Job {job['id']} enqueued (data_id={1000 + i})")
    print()

    print("=" * 60)
    print("Async Jobs Enqueued!")
    print("=" * 60)
    print()
    print("Run 'python worker.py' to process these jobs")
    print()

    await backend.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
