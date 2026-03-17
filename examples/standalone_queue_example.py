"""Example: Using Queue API in standalone mode with sqlery.

This demonstrates how to use the Queue API in standalone/FastAPI mode
without Django.
"""

from sqlery import initialize, get_queue, job
from datetime import datetime, timedelta

# ============================================================================
# Step 1: Initialize sqlery in standalone mode
# ============================================================================

initialize(
    database_url="postgresql://localhost/sqlery_jobs",
    max_workers=3,
    worker_queues=['email', 'reports', 'default'],
)

# ============================================================================
# Step 2: Define your tasks
# ============================================================================

@job
def send_email(to, subject, body):
    """Send an email."""
    print(f"Sending email to {to}: {subject}")
    # ... actual email sending logic
    return f"Email sent to {to}"


@job
def generate_report(report_type, format='pdf'):
    """Generate a report."""
    print(f"Generating {format} report: {report_type}")
    # ... report generation logic
    return f"Report generated: {report_type}.{format}"


@job
def cleanup_files(directory, days_old=30):
    """Clean up old files."""
    print(f"Cleaning up files in {directory} older than {days_old} days")
    # ... cleanup logic
    return f"Cleaned up {directory}"


# ============================================================================
# Step 3: Create queue instances with defaults
# ============================================================================

# Email queue: High priority, parallel execution, 30s timeout
email_queue = get_queue(
    'email',
    priority=10,
    max_retries=3,
    allow_parallel=True,
    timeout_seconds=30
)

# Reports queue: Lower priority, sequential execution, 5min timeout
reports_queue = get_queue(
    'reports',
    priority=5,
    allow_parallel=False,
    timeout_seconds=300
)

# Cleanup queue: Lowest priority, parallel execution
cleanup_queue = get_queue(
    'cleanup',
    priority=1,
    allow_parallel=True,
    timeout_seconds=120
)

# ============================================================================
# Step 4: Enqueue jobs to specific queues
# ============================================================================

# Immediate execution
job1 = email_queue.enqueue(
    'examples.standalone_queue_example.send_email',
    to='user@example.com',
    subject='Welcome!',
    body='Thanks for signing up'
)
print(f"Enqueued job {job1.id} to {email_queue.name} queue")

job2 = reports_queue.enqueue(
    'examples.standalone_queue_example.generate_report',
    report_type='monthly_sales',
    format='pdf'
)
print(f"Enqueued job {job2.id} to {reports_queue.name} queue")

# Schedule for later (1 hour from now)
run_time = datetime.now() + timedelta(hours=1)
job3 = cleanup_queue.enqueue_at(
    run_time,
    'examples.standalone_queue_example.cleanup_files',
    directory='/tmp/old_files',
    days_old=30
)
print(f"Scheduled job {job3.id} to run at {run_time}")

# ============================================================================
# Step 5: Override queue defaults per job
# ============================================================================

# Urgent email with higher priority than queue default
urgent_job = email_queue.enqueue(
    'examples.standalone_queue_example.send_email',
    priority=100,  # Override default priority=10
    timeout_seconds=10,  # Override default timeout_seconds=30
    to='admin@example.com',
    subject='URGENT: System Alert',
    body='Immediate action required'
)
print(f"Enqueued urgent job {urgent_job.id} with priority={urgent_job.priority}")

# ============================================================================
# Step 6: Use @job decorator methods
# ============================================================================

# Using the decorator's enqueue method
job4 = send_email.enqueue(
    to='another@example.com',
    subject='Test',
    body='Hello'
)
print(f"Enqueued job {job4.id} using @job decorator")

# Using the decorator's enqueue_at method
scheduled_time = datetime.now() + timedelta(minutes=30)
job5 = generate_report.enqueue_at(
    scheduled_time,
    report_type='weekly_summary',
    format='xlsx'
)
print(f"Scheduled job {job5.id} using @job decorator")

# ============================================================================
# Step 7: Run workers to process jobs
# ============================================================================

print("\nTo process these jobs, run:")
print("  sqlery worker --queues email,reports,cleanup")
print("\nOr run the web UI:")
print("  sqlery web")
print("  Then visit: http://localhost:8000")
