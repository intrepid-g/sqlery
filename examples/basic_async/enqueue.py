"""Enqueue async jobs example.

Enqueueing is a normal (blocking) call. Only the worker is async.
"""

from sqlery.compat import initialize
from tasks import process_data_async, generate_async_report, send_notifications_batch


def main():
    """Enqueue several async jobs."""
    initialize(database_url="sqlite:///example_async.db", enable_daemon=False)

    print("1. Enqueueing data processing job...")
    job1 = process_data_async.delay(data_id=12345, processing_time=2)
    print(f"   Job {job1.id} enqueued (queue: {job1.queue_name})")

    print("2. Enqueueing report generation job...")
    job2 = generate_async_report.enqueue(report_type="analytics", user_id=67890)
    print(f"   Job {job2.id} enqueued (queue: {job2.queue_name})")

    print("3. Enqueueing batch notifications job...")
    job3 = send_notifications_batch.delay(user_ids=[1, 2, 3, 4, 5])
    print(f"   Job {job3.id} enqueued")

    print("4. Enqueueing multiple processing jobs...")
    for i in range(5):
        job = process_data_async.delay(data_id=1000 + i, processing_time=1)
        print(f"   Job {job.id} enqueued (data_id={1000 + i})")

    print()
    print("Run 'python worker.py' to process these jobs")


if __name__ == "__main__":
    main()
