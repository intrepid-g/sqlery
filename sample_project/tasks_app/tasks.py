"""Sample tasks demonstrating sqlery features."""

import time
import random
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from sqlery import job

# Simple file-backed counter so the demo needs no extra model/migration.
_COUNTER_FILE = Path(__file__).resolve().parent / "hourly_counter.txt"


def _bump_counter() -> int:
    n = int(_COUNTER_FILE.read_text()) if _COUNTER_FILE.exists() else 0
    n += 1
    _COUNTER_FILE.write_text(str(n))
    return n


@job(queue="default")
def hourly_counter_task():
    """Increment a counter, then self-schedule the next run at now + 2 seconds.

    Registered as an hourly cron (see register_hourly_counter), AND each run
    also enqueues itself 2 seconds out via enqueue_at — so after the first cron
    fire it keeps ticking every 2s independently of the hourly schedule.
    """
    count = _bump_counter()
    print(f"⏲️  hourly_counter_task run #{count} at {timezone.now().isoformat()}")
    next_run = timezone.now() + timedelta(seconds=2)
    hourly_counter_task.enqueue_at(next_run)  # schedule next run now + 2s
    return {"count": count, "next_run": next_run.isoformat()}


def register_hourly_counter():
    """Idempotently register hourly_counter_task as an hourly cron ScheduledTask.

    Call once (e.g. from a manage.py shell or a data migration):
        from tasks_app.tasks import register_hourly_counter
        register_hourly_counter()
    """
    from sqlery.django_sqlery.models import ScheduledTask

    task, _ = ScheduledTask.objects.update_or_create(
        name="hourly_counter",
        defaults={
            "task_path": "tasks_app.tasks.hourly_counter_task",
            "cron_expression": "0 * * * *",  # top of every hour
            "enabled": True,
        },
    )
    return task


@job
def simple_task():
    """A simple task that just returns a message."""
    print("🎯 Running simple_task")
    return "Task completed successfully!"


@job(queue='email', priority=10, allow_parallel=True, timeout_seconds=30)
def send_email(to_email, subject='Test'):
    """Simulates sending an email."""
    print(f"📧 Sending email to {to_email} with subject: {subject}")
    time.sleep(1)  # Simulate email sending
    return f"Email sent to {to_email}"


@job(queue='reports', priority=5, timeout_seconds=60)
def generate_report(report_type='daily'):
    """Simulates generating a report."""
    print(f"📊 Generating {report_type} report")
    time.sleep(2)  # Simulate report generation
    return f"{report_type.capitalize()} report generated"


@job(queue='cleanup', allow_parallel=True)
def cleanup_old_files(days_old=30):
    """Simulates cleaning up old files."""
    print(f"🧹 Cleaning up files older than {days_old} days")
    time.sleep(1)
    return f"Cleaned up files older than {days_old} days"


@job(max_retries=3, retry_backoff=2.0)
def flaky_task():
    """A task that fails randomly to demonstrate retry logic."""
    print("🎲 Running flaky_task")
    if random.random() < 0.5:
        raise Exception("Random failure to demonstrate retry logic")
    return "Flaky task succeeded!"


@job(timeout_seconds=5)
def quick_task(message='Hello'):
    """A quick task with a 5-second timeout."""
    print(f"⚡ Quick task: {message}")
    return f"Quick task processed: {message}"


@job(queue='migrations', allow_parallel=False, timeout_seconds=300)
def run_database_migration(migration_name):
    """Simulates running a database migration (must run exclusively)."""
    print(f"🔄 Running migration: {migration_name}")
    time.sleep(3)  # Simulate migration
    return f"Migration {migration_name} completed"


@job
def scheduled_daily_task():
    """A task designed to be run on a schedule."""
    print("📅 Running daily scheduled task")
    time.sleep(1)
    return f"Daily task completed at {time.strftime('%Y-%m-%d %H:%M:%S')}"


@job
def long_running_task():
    """A long-running task to test timeouts."""
    print("⏰ Starting long-running task")
    time.sleep(10)  # Will timeout if timeout_seconds < 10
    return "Long-running task completed"


@job(queue='email', allow_parallel=True)
def send_bulk_emails(count=10):
    """Sends multiple emails in parallel."""
    print(f"📬 Sending {count} emails in bulk")
    time.sleep(2)
    return f"Sent {count} emails successfully"
