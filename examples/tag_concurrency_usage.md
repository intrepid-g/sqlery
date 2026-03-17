# Tag-Based Concurrency Limiting - Usage Guide

## Overview

Tag-based concurrency limiting prevents API throttling and resource contention by limiting how many jobs with specific tags can run simultaneously across all workers.

## Problem Statement

When syncing data from external APIs, running multiple jobs in parallel can cause:
- **API Rate Limiting**: External API throttles your requests
- **Resource Exhaustion**: Database connection limits exceeded
- **Contention**: Multiple jobs fighting for the same resource

**Traditional Solutions**:
- Separate queues per API (complex configuration)
- Single-threaded processing (slow)
- Manual coordination (error-prone)

**Sqlery Solution**: Tag-based concurrency limits

## Configuration

### Step 1: Define Tag Concurrency Limits

```python
# settings.py
DJANGO_SQL_JOBS = {
    "TAG_CONCURRENCY_LIMITS": {
        "acme-api": 1,          # Max 1 concurrent job for Acme API
        "legacy-db": 2,         # Max 2 concurrent jobs for legacy database
        "image-processing": 5,  # Max 5 concurrent image processing jobs
        "rate-limited": 3,      # Max 3 concurrent rate-limited operations
    },
}
```

### Step 2: Tag Your Jobs

You can tag jobs using three methods:

#### Method 1: Direct API Call

```python
from sqlery import enqueue

# Single tag
enqueue(
    'myapp.tasks.sync_acme_customer',
    tags=['acme-api'],
    customer_id=123
)

# Multiple tags
enqueue(
    'myapp.tasks.process_image',
    tags=['image-processing', 'rate-limited'],
    image_url='https://example.com/image.jpg'
)
```

#### Method 2: Decorator with Default Tags

```python
from sqlery.decorators import job

@job(tags=['acme-api'])
def sync_acme_customer(customer_id):
    """Sync customer data from Acme API."""
    # Only 1 instance can run at a time across all workers
    response = requests.get(f'https://acme-api.com/customers/{customer_id}')
    Customer.objects.update_or_create(...)

# Usage
sync_acme_customer.enqueue(customer_id=123)
```

#### Method 3: Override Tags at Runtime

```python
@job(tags=['acme-api'])
def sync_acme_data(endpoint, data_id):
    """Generic Acme API sync."""
    pass

# Use decorator default
sync_acme_data.enqueue(endpoint='customers', data_id=123)

# Override with different tags
sync_acme_data.enqueue(
    endpoint='products',
    data_id=456,
    tags=['acme-api', 'urgent']  # Overrides decorator tags
)
```

## Real-World Examples

### Example 1: External API Sync

```python
# tasks.py
from sqlery.decorators import job
import requests

@job(
    queue='api-sync',
    tags=['acme-api'],
    max_retries=3,
    timeout_seconds=30
)
def sync_customer(customer_id):
    """Sync customer from Acme API (max 1 concurrent)."""
    response = requests.get(
        f'https://acme-api.com/customers/{customer_id}',
        headers={'API-Key': settings.ACME_API_KEY}
    )
    response.raise_for_status()

    customer_data = response.json()
    Customer.objects.update_or_create(
        external_id=customer_id,
        defaults=customer_data
    )

# Enqueue 100 customer syncs
# Even with multiple workers, only 1 will run at a time
for customer_id in range(1, 101):
    sync_customer.enqueue(customer_id=customer_id)
```

### Example 2: Database Connection Limiting

```python
@job(
    tags=['legacy-db'],
    timeout_seconds=300
)
def migrate_order(order_id):
    """Migrate order from legacy database (max 2 concurrent)."""
    # Connect to legacy database
    legacy_conn = psycopg2.connect(settings.LEGACY_DB_URL)

    # This operation is limited to 2 concurrent executions
    # preventing connection pool exhaustion
    with legacy_conn.cursor() as cursor:
        cursor.execute("SELECT * FROM orders WHERE id = %s", [order_id])
        order_data = cursor.fetchone()

    # Migrate to new database
    Order.objects.create(...)

# Enqueue 1000 migrations
# Only 2 will run concurrently, protecting legacy database
for order_id in legacy_order_ids:
    migrate_order.enqueue(order_id=order_id)
```

### Example 3: Resource-Intensive Operations

```python
@job(
    tags=['image-processing'],
    queue='media',
    timeout_seconds=600
)
def process_image(image_id):
    """Process image (max 5 concurrent to limit CPU/memory usage)."""
    image = Image.objects.get(id=image_id)

    # Download original
    img = download_image(image.url)

    # Generate thumbnails (CPU/memory intensive)
    thumbnails = {
        'small': img.resize((100, 100)),
        'medium': img.resize((300, 300)),
        'large': img.resize((800, 800)),
    }

    # Upload to S3
    for size, thumb in thumbnails.items():
        upload_to_s3(thumb, f'{image_id}_{size}.jpg')

# Process 50 images
# Only 5 will process concurrently, preventing memory exhaustion
for image_id in image_ids:
    process_image.enqueue(image_id=image_id)
```

### Example 4: Multiple Tags

```python
@job(tags=['shopify-api', 'webhook-handling'])
def process_shopify_webhook(webhook_type, data):
    """Process Shopify webhook."""
    # Limited by TWO constraints:
    # 1. Max shopify-api concurrency (e.g., 2)
    # 2. Max webhook-handling concurrency (e.g., 10)
    #
    # If either limit is reached, job waits
    pass

# Configuration
DJANGO_SQL_JOBS = {
    "TAG_CONCURRENCY_LIMITS": {
        "shopify-api": 2,        # Shopify rate limit
        "webhook-handling": 10,  # General webhook capacity
    },
}
```

## How It Works

### Job Claiming Process

1. **Worker requests next job**
2. **Database returns highest priority queued job**
3. **Worker checks tag concurrency limits**:
   - For each tag on the job, count running jobs with that tag
   - If any tag limit would be exceeded, skip this job
   - Try next job (up to MAX_JOB_CLAIM_ATTEMPTS, default 10)
4. **Worker claims job if limits OK**
5. **Worker executes job**
6. **Worker releases job**, freeing up tag capacity

### Concurrency Check Example

```
Job A: tags=['acme-api']
Job B: tags=['acme-api']
Job C: tags=['acme-api']

Settings: TAG_CONCURRENCY_LIMITS = {"acme-api": 1}

Timeline:
t0: Worker 1 claims Job A (running count: 1, limit: 1) ✓
t1: Worker 2 tries Job B (running count: 1, limit: 1) ✗ SKIPPED
t2: Worker 3 tries Job C (running count: 1, limit: 1) ✗ SKIPPED
t3: Job A completes (running count: 0, limit: 1)
t4: Worker 2 claims Job B (running count: 1, limit: 1) ✓
```

## Best Practices

### 1. Use Descriptive Tag Names

```python
# Good
tags=['stripe-api', 'payment-processing']

# Bad
tags=['api', 'slow']
```

### 2. Set Appropriate Limits

```python
# API with 10 req/sec limit, 2 workers, jobs take ~1 second
# → Set limit to 2-3 to stay under rate limit
"stripe-api": 2,

# Database with 20 connection pool
# → Set limit to 15 to leave headroom
"legacy-db": 15,

# CPU-intensive task on 4-core machine
# → Set limit to 4 or less
"video-encoding": 3,
```

### 3. Combine with Queue Priority

```python
# High-priority API calls
enqueue(
    'tasks.urgent_sync',
    queue='high-priority',  # Processed first
    priority=100,           # Highest priority
    tags=['acme-api']       # Still limited to 1 concurrent
)
```

### 4. Monitor Tag Utilization

```python
# Check current usage
from sqlery.models import QueuedJob

running_by_tag = {}
for tag, limit in settings.DJANGO_SQL_JOBS['TAG_CONCURRENCY_LIMITS'].items():
    count = QueuedJob.objects.filter(
        status='running',
        tags__contains=[tag]
    ).count()

    running_by_tag[tag] = {
        'running': count,
        'limit': limit,
        'utilization': f"{count}/{limit}"
    }

print(running_by_tag)
# {'acme-api': {'running': 1, 'limit': 1, 'utilization': '1/1'}}
```

## Troubleshooting

### Jobs Not Processing

**Symptom**: Jobs stay queued indefinitely

**Possible Causes**:
1. Tag limit is 0 or very low
2. All jobs have tags at max concurrency
3. Workers not running

**Solution**:
```python
# Check running jobs by tag
from sqlery.models import QueuedJob

for tag in ['acme-api', 'legacy-db']:
    running = QueuedJob.objects.filter(
        status='running',
        tags__contains=[tag]
    ).count()
    print(f"{tag}: {running} running")

# Check queued jobs
queued = QueuedJob.objects.filter(status='queued').count()
print(f"Queued jobs: {queued}")
```

### Worker Keeps Skipping Jobs

**Symptom**: Worker logs show jobs being skipped

**Cause**: Tag limits are reached, worker tries MAX_JOB_CLAIM_ATTEMPTS times

**Solution**:
- Increase tag limit if appropriate
- Add more workers to process non-tag-limited jobs
- Check if stuck jobs are holding tags

```python
# Find long-running jobs
from django.utils import timezone
from datetime import timedelta

stuck_jobs = QueuedJob.objects.filter(
    status='running',
    started_at__lt=timezone.now() - timedelta(hours=1)
)

for job in stuck_jobs:
    print(f"Job {job.id} running for {job.duration_seconds}s with tags: {job.tags}")
```

## Performance Considerations

- **Database Queries**: Each tag check requires a COUNT query
- **Impact**: Minimal for most use cases (indexed queries)
- **Optimization**: If you have many tags, consider consolidating

## Migration Guide

If you have existing jobs without tags:

```python
# All existing jobs will have tags=[]
# This is fine - jobs without tags have no limits

# To add tags to existing jobs:
from sqlery.models import QueuedJob

QueuedJob.objects.filter(
    task_path='myapp.tasks.sync_acme_customer',
    status='queued'
).update(tags=['acme-api'])
```

## Summary

Tag-based concurrency limiting provides:
- ✅ Fine-grained control over concurrent execution
- ✅ Protection against API rate limiting
- ✅ Resource utilization management
- ✅ Flexible tagging (multiple tags per job)
- ✅ Works across distributed workers
- ✅ No separate queues needed
- ✅ Database-enforced limits (atomic)

Perfect for managing external API integrations, database connections, and resource-intensive operations!
