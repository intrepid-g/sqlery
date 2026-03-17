"""Webhook delivery for job completion notifications."""

import logging
import hmac
import hashlib
import json
import uuid as _uuid
from datetime import datetime, date, time, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles UUID, datetime, Decimal, and other common types."""

    def default(self, o):
        if isinstance(o, _uuid.UUID):
            return str(o)
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        if isinstance(o, timedelta):
            return o.total_seconds()
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (set, frozenset)):
            return list(o)
        if isinstance(o, bytes):
            return o.decode("utf-8", errors="replace")
        return super().default(o)


def generate_webhook_signature(payload, secret):
    """Generate HMAC-SHA256 signature for webhook payload.

    Args:
        payload (dict): Webhook payload data
        secret (str): Secret key for signing

    Returns:
        str: Hex-encoded signature
    """
    if not secret:
        return None

    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'), cls=_SafeEncoder)
    signature = hmac.new(
        secret.encode('utf-8'),
        payload_json.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return signature


def send_webhook(job, event='success'):
    """Send webhook notification for job completion.

    Sends HTTP POST request to webhook_url with job status and metadata.
    Includes HMAC signature for authentication if WEBHOOK_SECRET is configured.

    Args:
        job: QueuedJob instance
        event (str): Event type ('success' or 'failure')

    Returns:
        bool: True if webhook sent successfully, False otherwise
    """
    if not job.webhook_url:
        return True  # No webhook configured, nothing to do

    # Check if this event should trigger a webhook
    if event not in job.webhook_events:
        logger.debug(f"Skipping webhook for job {job.id} - event '{event}' not in {job.webhook_events}")
        return True

    from .settings import get_setting

    # Build webhook payload
    payload = {
        'event': event,
        'job_id': job.id,
        'task_path': job.task_path,
        'status': job.status,
        'queue_name': job.queue_name,
        'priority': job.priority,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        'duration_seconds': job.duration_seconds,
        'output': job.output,
        'error': job.error,
        'retry_count': job.retry_count,
        'tags': job.tags,
    }

    # Generate signature if secret is configured
    webhook_secret = get_setting('WEBHOOK_SECRET', None)
    signature = generate_webhook_signature(payload, webhook_secret)

    # Prepare headers
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Sqlery-Webhooks/1.0',
    }

    if signature:
        headers['X-Sqlery-Signature'] = f'sha256={signature}'

    # Send webhook
    timeout = get_setting('WEBHOOK_TIMEOUT', 10)  # 10 second default timeout

    try:
        import requests
    except ImportError:
        logger.error(
            "Cannot send webhook: requests library not installed. "
            "Install with: pip install sqlery[webhooks]"
        )
        return False

    try:
        response = requests.post(
            job.webhook_url,
            data=json.dumps(payload, cls=_SafeEncoder),
            headers=headers,
            timeout=timeout
        )

        if response.status_code in (200, 201, 202, 204):
            logger.info(f"Webhook sent successfully for job {job.id}: {response.status_code}")
            job.webhook_status = 'sent'
            job.save(update_fields=['webhook_status'])
            return True
        else:
            logger.warning(
                f"Webhook failed for job {job.id}: HTTP {response.status_code} - {response.text[:200]}"
            )
            return False

    except requests.exceptions.Timeout:
        logger.warning(f"Webhook timeout for job {job.id} after {timeout}s")
        return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"Webhook request failed for job {job.id}: {e}")
        return False

    except Exception as e:
        logger.error(f"Unexpected error sending webhook for job {job.id}: {e}", exc_info=True)
        return False


def send_webhook_with_retry(job, event='success'):
    """Send webhook with automatic retry on failure.

    Attempts to send webhook, retrying with exponential backoff on failure.

    Args:
        job: QueuedJob instance
        event (str): Event type ('success' or 'failure')

    Returns:
        bool: True if webhook sent successfully (or no webhook configured)
    """
    if not job.webhook_url:
        return True  # No webhook configured

    # Mark webhook as pending on first attempt
    if job.webhook_retries == 0:
        job.webhook_status = 'pending'
        job.save(update_fields=['webhook_status'])

    # Try to send webhook
    success = send_webhook(job, event)

    if success:
        return True

    # Webhook failed, increment retry counter
    job.webhook_retries += 1
    job.save(update_fields=['webhook_retries'])

    # Check if we should retry
    if job.webhook_retries < job.webhook_max_retries:
        logger.info(
            f"Webhook failed for job {job.id}, will retry "
            f"(attempt {job.webhook_retries}/{job.webhook_max_retries})"
        )
        # Note: Actual retry scheduling would happen via a separate mechanism
        # (e.g., scheduled task to retry failed webhooks)
        job.webhook_status = 'pending'
        job.save(update_fields=['webhook_status'])
        return False
    else:
        # Max retries exhausted
        logger.error(
            f"Webhook permanently failed for job {job.id} after "
            f"{job.webhook_retries} attempts"
        )
        job.webhook_status = 'failed'
        job.save(update_fields=['webhook_status'])
        return False


def retry_failed_webhooks():
    """Retry all jobs with failed webhook deliveries.

    This function should be called periodically (e.g., via scheduled task)
    to retry webhooks that failed on previous attempts.

    Returns:
        dict: Statistics about retry attempts
    """
    from .models import QueuedJob
    from django.db.models import F

    # Find jobs with pending webhooks that haven't exceeded max retries
    jobs_to_retry = QueuedJob.objects.filter(
        webhook_status='pending',
        webhook_retries__lt=F('webhook_max_retries')
    )

    stats = {
        'total': 0,
        'success': 0,
        'failed': 0,
        'skipped': 0,
    }

    for job in jobs_to_retry:
        stats['total'] += 1

        # Determine event type based on job status
        if job.status == 'success':
            event = 'success'
        elif job.status == 'failed':
            event = 'failure'
        else:
            # Job not yet complete, skip
            stats['skipped'] += 1
            continue

        # Try to send webhook
        success = send_webhook_with_retry(job, event)

        if success:
            stats['success'] += 1
        else:
            stats['failed'] += 1

    logger.info(
        f"Webhook retry batch complete: {stats['success']} succeeded, "
        f"{stats['failed']} failed, {stats['skipped']} skipped"
    )

    return stats
