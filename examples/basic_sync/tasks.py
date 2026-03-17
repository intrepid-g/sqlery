"""Task definitions for basic sync example."""

from sqlery import job
import time


@job(queue='default', timeout=60)
def send_email(to, subject, body):
    """Send an email (simulated)."""
    print(f"📧 Sending email to {to}")
    print(f"   Subject: {subject}")
    print(f"   Body: {body}")
    time.sleep(1)  # Simulate email sending
    print(f"✓ Email sent to {to}")
    return f"Email sent to {to}"


@job(queue='default', priority=10, timeout=120)
def process_payment(user_id, amount):
    """Process a payment (simulated)."""
    print(f"💳 Processing payment for user {user_id}")
    print(f"   Amount: ${amount}")
    time.sleep(2)  # Simulate payment processing
    print(f"✓ Payment processed for user {user_id}")
    return f"Payment of ${amount} processed for user {user_id}"


@job(queue='reports', timeout=180)
def generate_report(report_type, user_id):
    """Generate a report (simulated)."""
    print(f"📊 Generating {report_type} report for user {user_id}")
    time.sleep(3)  # Simulate report generation
    print(f"✓ Report generated")
    return f"{report_type} report generated for user {user_id}"
