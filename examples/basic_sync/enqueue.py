"""Enqueue jobs example."""

from sqlery.compat import initialize
from tasks import send_email, process_payment, generate_report


def main():
    """Enqueue several jobs."""
    initialize(database_url="sqlite:///example.db", enable_daemon=False)

    print("1. Enqueueing email job...")
    job1 = send_email.delay(
        to="user@example.com", subject="Welcome!", body="Thanks for signing up!"
    )
    print(f"   Job {job1.id} enqueued (queue: {job1.queue_name})")

    print("2. Enqueueing payment job (high priority)...")
    job2 = process_payment.enqueue(user_id=12345, amount=99.99)
    print(f"   Job {job2.id} enqueued (priority: {job2.priority})")

    print("3. Enqueueing report job...")
    job3 = generate_report.delay(report_type="sales", user_id=67890)
    print(f"   Job {job3.id} enqueued (queue: {job3.queue_name})")

    print()
    print("Run 'python worker.py' to process these jobs")


if __name__ == "__main__":
    main()
