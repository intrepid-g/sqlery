# Webhooks - Usage Guide

Webhooks allow Sqlery to send HTTP POST notifications to your application when jobs complete (success or failure). This enables real-time integration with external services and event-driven workflows.

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Use Cases](#use-cases)
- [Webhook Payload](#webhook-payload)
- [Security](#security)
- [Retry Logic](#retry-logic)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

## Quick Start

```python
from sqlery import enqueue

# Send webhook on job completion
job = enqueue(
    'myapp.tasks.process_payment',
    webhook_url='https://example.com/hooks/payment-complete',
    webhook_events=['success', 'failure'],  # Optional: defaults to both
    amount=100
)
```

When the job completes, Sqlery will POST to the webhook URL with job status and metadata.

## Configuration

### Settings

Add these settings to your Django settings.py:

```python
SQLERY = {
    # Webhook secret for HMAC signature (highly recommended)
    'WEBHOOK_SECRET': 'your-secret-key-here',

    # Webhook request timeout in seconds (default: 10)
    'WEBHOOK_TIMEOUT': 10,
}
```

### Install Dependencies

Webhooks require the `requests` library:

```bash
pip install sqlery[webhooks]
# or
pip install requests
```

## Use Cases

### Payment Processing Notifications

```python
from sqlery import enqueue

# Process payment and notify your app on completion
job = enqueue(
    'payments.tasks.charge_customer',
    webhook_url='https://api.example.com/webhooks/payment',
    webhook_events=['success', 'failure'],
    customer_id=123,
    amount=49.99
)
```

### Slack/Discord Notifications

```python
# Notify only on failure
job = enqueue(
    'data.tasks.daily_import',
    webhook_url='https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK',
    webhook_events=['failure'],  # Only notify on errors
)
```

### External System Integration

```python
# Update external CRM when job completes
job = enqueue(
    'crm.tasks.sync_customer_data',
    webhook_url='https://api.salesforce.com/webhooks/job-complete',
    webhook_events=['success'],
    customer_id=456
)
```

### Multi-step Workflow Orchestration

```python
# Chain jobs with webhooks for each step
extract_job = enqueue(
    'etl.tasks.extract_data',
    webhook_url='https://example.com/hooks/extract-complete',
    source='api'
)

# The webhook can trigger the next step in your external orchestration system
```

## Webhook Payload

### Structure

Sqlery sends a JSON payload with the following structure:

```json
{
  "event": "success",
  "job_id": 123,
  "task_path": "myapp.tasks.process_payment",
  "status": "success",
  "queue_name": "default",
  "priority": 0,
  "created_at": "2024-01-15T10:00:00Z",
  "started_at": "2024-01-15T10:00:05Z",
  "finished_at": "2024-01-15T10:00:10Z",
  "duration_seconds": 5.0,
  "output": "Payment processed successfully",
  "error": null,
  "retry_count": 0,
  "tags": ["payment", "stripe"]
}
```

### Fields

- `event` (str): Event type - `"success"` or `"failure"`
- `job_id` (int): Unique job identifier
- `task_path` (str): Python path to the task function
- `status` (str): Final job status - `"success"` or `"failed"`
- `queue_name` (str): Queue the job ran in
- `priority` (int): Job priority
- `created_at` (str): ISO 8601 timestamp when job was created
- `started_at` (str): ISO 8601 timestamp when job started
- `finished_at` (str): ISO 8601 timestamp when job finished
- `duration_seconds` (float): Job execution time in seconds
- `output` (str): Task return value (for success)
- `error` (str): Error message (for failure)
- `retry_count` (int): Number of retry attempts
- `tags` (list): Job tags

### Example Handler (Flask)

```python
from flask import Flask, request, jsonify
import hmac
import hashlib
import json

app = Flask(__name__)

WEBHOOK_SECRET = 'your-secret-key-here'

@app.route('/webhooks/job-complete', methods=['POST'])
def handle_job_complete():
    # Verify signature
    signature = request.headers.get('X-Sqlery-Signature', '')

    if signature:
        # Remove "sha256=" prefix
        expected_signature = signature.replace('sha256=', '')

        # Compute HMAC
        payload_json = json.dumps(request.json, sort_keys=True, separators=(',', ':'))
        computed_signature = hmac.new(
            WEBHOOK_SECRET.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Verify
        if not hmac.compare_digest(expected_signature, computed_signature):
            return jsonify({'error': 'Invalid signature'}), 401

    # Process webhook
    payload = request.json
    event = payload['event']
    job_id = payload['job_id']

    if event == 'success':
        print(f"Job {job_id} completed successfully!")
        # Handle success...
    else:
        print(f"Job {job_id} failed: {payload['error']}")
        # Handle failure...

    return jsonify({'status': 'received'}), 200
```

### Example Handler (Django)

```python
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import hmac
import hashlib
import json

WEBHOOK_SECRET = 'your-secret-key-here'

@csrf_exempt
@require_POST
def job_complete_webhook(request):
    # Verify signature
    signature = request.headers.get('X-Sqlery-Signature', '')

    if signature:
        expected_signature = signature.replace('sha256=', '')

        payload_json = json.dumps(json.loads(request.body), sort_keys=True, separators=(',', ':'))
        computed_signature = hmac.new(
            WEBHOOK_SECRET.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, computed_signature):
            return JsonResponse({'error': 'Invalid signature'}, status=401)

    # Process webhook
    payload = json.loads(request.body)
    event = payload['event']
    job_id = payload['job_id']

    if event == 'success':
        # Handle success...
        pass
    else:
        # Handle failure...
        pass

    return JsonResponse({'status': 'received'})
```

## Security

### HMAC Signature Verification

Sqlery signs all webhook requests with HMAC-SHA256 to prove authenticity.

**Configure secret in Django settings:**

```python
SQLERY = {
    'WEBHOOK_SECRET': 'use-a-long-random-string-here',
}
```

**Verify signature in your webhook handler:**

```python
import hmac
import hashlib
import json

def verify_webhook_signature(request):
    signature = request.headers.get('X-Sqlery-Signature', '')

    if not signature:
        return False

    # Remove "sha256=" prefix
    expected_signature = signature.replace('sha256=', '')

    # Compute HMAC on the JSON payload
    payload_json = json.dumps(request.json, sort_keys=True, separators=(',', ':'))
    secret = 'your-secret-key-here'

    computed_signature = hmac.new(
        secret.encode('utf-8'),
        payload_json.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # Constant-time comparison
    return hmac.compare_digest(expected_signature, computed_signature)
```

### Best Practices

1. **Always verify signatures** - Reject webhooks with invalid/missing signatures
2. **Use HTTPS** - Never send webhooks to HTTP endpoints in production
3. **Keep secrets secure** - Store WEBHOOK_SECRET in environment variables
4. **Return 2xx quickly** - Process webhooks asynchronously if needed
5. **Implement idempotency** - Handle duplicate webhook deliveries gracefully

## Retry Logic

### Automatic Retries

Webhooks are automatically retried on failure with these defaults:

- **Max retries**: 3 attempts
- **Retry on**: HTTP errors, timeouts, connection failures
- **Success codes**: 200, 201, 202, 204

### Customize Retry Behavior

```python
# Custom webhook retry settings per job
job = enqueue(
    'myapp.tasks.important_job',
    webhook_url='https://example.com/hooks/critical',
)

# After creation, update webhook_max_retries if needed
job.webhook_max_retries = 5  # More retries for critical webhooks
job.save(update_fields=['webhook_max_retries'])
```

### Manual Webhook Retry

For jobs with failed webhooks, you can manually retry:

```python
from sqlery.webhooks import retry_failed_webhooks

# Retry all pending webhooks
stats = retry_failed_webhooks()

print(f"Retried {stats['total']} webhooks:")
print(f"  - Success: {stats['success']}")
print(f"  - Failed: {stats['failed']}")
print(f"  - Skipped: {stats['skipped']}")
```

### Scheduled Webhook Retry

Set up a scheduled task to periodically retry failed webhooks:

```python
# In your ScheduledTask admin or via code:
from sqlery.models import ScheduledTask

ScheduledTask.objects.create(
    name='retry-failed-webhooks',
    task_path='sqlery.webhooks.retry_failed_webhooks',
    cron_expression='*/5 * * * *',  # Every 5 minutes
    queue_name='default',
    is_enabled=True
)
```

## Webhook Events

### Available Events

- `success` - Job completed successfully
- `failure` - Job failed (error, timeout, cancelled, etc.)

### Selective Notifications

```python
# Only notify on success
job = enqueue(
    'reports.tasks.generate_daily_report',
    webhook_url='https://example.com/hooks/report-ready',
    webhook_events=['success']  # Don't notify on failure
)

# Only notify on failure (error monitoring)
job = enqueue(
    'backups.tasks.backup_database',
    webhook_url='https://pagerduty.com/hooks/database-backup',
    webhook_events=['failure']  # Alert only on errors
)

# Notify on both (default)
job = enqueue(
    'payments.tasks.process_refund',
    webhook_url='https://example.com/hooks/refund',
    webhook_events=['success', 'failure']  # Explicitly set default
)
```

## Best Practices

### 1. Return 2xx Status Code Quickly

Webhook handlers should return success (200-204) immediately:

```python
@app.route('/webhooks/job-complete', methods=['POST'])
def handle_webhook():
    payload = request.json

    # Queue for background processing
    process_webhook_async.delay(payload)

    # Return success immediately
    return jsonify({'status': 'received'}), 200
```

### 2. Handle Idempotency

Webhooks may be delivered multiple times (retries). Handle duplicates:

```python
@app.route('/webhooks/job-complete', methods=['POST'])
def handle_webhook():
    payload = request.json
    job_id = payload['job_id']

    # Check if already processed
    if WebhookLog.objects.filter(job_id=job_id).exists():
        return jsonify({'status': 'already_processed'}), 200

    # Process webhook
    process_job_complete(payload)

    # Mark as processed
    WebhookLog.objects.create(job_id=job_id, payload=payload)

    return jsonify({'status': 'received'}), 200
```

### 3. Use Webhooks for Critical Workflows Only

Don't overuse webhooks. For simple cases, poll the job status instead:

```python
# ❌ Overkill - polling is simpler
job = enqueue(
    'tasks.simple_task',
    webhook_url='https://example.com/hooks/done'
)

# ✅ Better - poll for status
job = enqueue('tasks.simple_task')
# Later: check job.status
```

### 4. Separate Webhooks by Environment

Use different webhook URLs for dev/staging/production:

```python
from django.conf import settings

webhook_url = settings.WEBHOOK_BASE_URL + '/payment-complete'

job = enqueue(
    'payments.tasks.charge_card',
    webhook_url=webhook_url,  # Different URL per environment
    amount=99.99
)
```

### 5. Log Webhook Deliveries

Track webhook successes and failures for debugging:

```python
# In your webhook handler
@app.route('/webhooks/job-complete', methods=['POST'])
def handle_webhook():
    payload = request.json

    # Log the webhook delivery
    logger.info(f"Received webhook for job {payload['job_id']}: {payload['event']}")

    try:
        process_webhook(payload)
        logger.info(f"Webhook processed successfully for job {payload['job_id']}")
    except Exception as e:
        logger.error(f"Webhook processing failed for job {payload['job_id']}: {e}")
        # Still return 200 to prevent retries for processing errors

    return jsonify({'status': 'received'}), 200
```

## Troubleshooting

### Webhooks Not Being Sent

**Check:**
1. Is `webhook_url` set on the job?
2. Is `requests` library installed? (`pip install requests`)
3. Are there errors in the Sqlery logs?

```python
# Verify webhook configuration
job = QueuedJob.objects.get(id=123)
print(f"Webhook URL: {job.webhook_url}")
print(f"Webhook events: {job.webhook_events}")
print(f"Webhook status: {job.webhook_status}")
```

### Webhooks Failing

**Check webhook status:**

```python
from sqlery.models import QueuedJob

# Find jobs with failed webhooks
failed_webhooks = QueuedJob.objects.filter(webhook_status='failed')

for job in failed_webhooks:
    print(f"Job {job.id}: webhook_retries={job.webhook_retries}, max={job.webhook_max_retries}")
```

**Common causes:**
- Webhook endpoint is down or unreachable
- Endpoint returning non-2xx status code
- Timeout (default 10s)
- SSL certificate errors

### Testing Webhooks Locally

Use tools like ngrok or Hookdeck to receive webhooks during development:

```bash
# Start ngrok to expose localhost
ngrok http 8000

# Use the ngrok URL for webhooks
https://abc123.ngrok.io/webhooks/job-complete
```

```python
# Test with local webhook handler
job = enqueue(
    'tasks.test_task',
    webhook_url='https://abc123.ngrok.io/webhooks/job-complete',
)
```

### Webhook Signature Verification Failing

**Debug signature mismatch:**

```python
import hmac
import hashlib
import json

# Received from Sqlery
received_signature = 'sha256=abc123...'
payload = {...}  # Webhook JSON payload

# Your secret
secret = 'your-secret-key-here'

# Compute signature
payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
computed = hmac.new(
    secret.encode('utf-8'),
    payload_json.encode('utf-8'),
    hashlib.sha256
).hexdigest()

print(f"Received: {received_signature.replace('sha256=', '')}")
print(f"Computed: {computed}")
print(f"Match: {received_signature.replace('sha256=', '') == computed}")
```

## Advanced Usage

### Conditional Webhooks

Send different webhooks based on job parameters:

```python
def enqueue_payment(customer_id, amount, vip=False):
    webhook_url = (
        'https://example.com/hooks/vip-payment'
        if vip
        else 'https://example.com/hooks/payment'
    )

    return enqueue(
        'payments.tasks.charge_customer',
        webhook_url=webhook_url,
        customer_id=customer_id,
        amount=amount
    )
```

### Multiple Webhooks per Job

Create dependent jobs with different webhooks for each step:

```python
# Step 1: Extract data, notify extraction complete
extract_job = enqueue(
    'etl.extract',
    webhook_url='https://example.com/hooks/extract-done'
)

# Step 2: Transform data, notify transformation complete
transform_job = enqueue(
    'etl.transform',
    depends_on=[extract_job.id],
    webhook_url='https://example.com/hooks/transform-done'
)

# Step 3: Load data, notify pipeline complete
load_job = enqueue(
    'etl.load',
    depends_on=[transform_job.id],
    webhook_url='https://example.com/hooks/pipeline-done'
)
```

### Webhook Aggregation

Batch multiple webhook deliveries for efficiency:

```python
# In your webhook handler
@app.route('/webhooks/batch', methods=['POST'])
def handle_batch_webhook():
    # Receive multiple job completions in one request
    jobs = request.json['jobs']

    for job_data in jobs:
        process_job_complete(job_data)

    return jsonify({'status': 'received', 'count': len(jobs)}), 200
```
