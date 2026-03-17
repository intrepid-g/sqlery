# Rate Limiting (Throttling) - Usage Guide

## Overview

Rate limiting controls how many jobs can execute within a time window, preventing external API throttling and ensuring smooth job distribution over time.

## Difference from Concurrency Limits

**Concurrency Limits** (`TAG_CONCURRENCY_LIMITS`):
- Limits how many jobs **run simultaneously**
- Example: "Max 2 jobs running at the same time"
- Use case: Prevent resource exhaustion (DB connections, memory, CPU)

**Rate Limits** (`TAG_RATE_LIMITS`):
- Limits how many jobs **complete within a time window**
- Example: "Max 60 jobs per minute" (even if they finish in 0.1s each)
- Use case: Respect external API rate limits

**Combined Usage**:
```python
TAG_CONCURRENCY_LIMITS = {"acme-api": 1}  # Max 1 concurrent
TAG_RATE_LIMITS = {"acme-api": "60/m"}    # Max 60 per minute

# Result: 1 job at a time, 60 per minute = smooth 1/second distribution
```

## Rate Limit Format

Format: `"{count}/{unit}"` where:
- `count`: Number of allowed executions
- `unit`: Time unit
  - `s` - seconds
  - `m` - minutes
  - `h` - hours
  - `10s` - 10 seconds (custom duration)
  - `30m` - 30 minutes (custom duration)

### Examples

```python
"100/s"   # 100 requests per second
"60/m"    # 60 requests per minute (1 per second)
"5000/h"  # 5000 requests per hour (~1.4 per second)
"1/10s"   # 1 request per 10 seconds (0.1 per second)
"10/30s"  # 10 requests per 30 seconds (0.33 per second)
```

## Configuration

### Step 1: Define Tag Rate Limits

```python
# settings.py
DJANGO_SQL_JOBS = {
    # Concurrency limits (how many at once)
    "TAG_CONCURRENCY_LIMITS": {
        "acme-api": 1,          # Max 1 concurrent
        "stripe-api": 2,        # Max 2 concurrent
    },

    # Rate limits (how many per time window)
    "TAG_RATE_LIMITS": {
        "acme-api": "60/m",     # 60 per minute
        "stripe-api": "100/s",  # 100 per second
        "shopify-api": "2/s",   # 2 per second
        "slow-api": "1/10s",    # 1 per 10 seconds
    },
}
```

### Step 2: Tag Your Jobs

Jobs automatically inherit rate limits from their tags:

```python
from sqlery.decorators import job

@job(tags=["acme-api"])
def sync_acme_customer(customer_id):
    """Sync customer (max 60 per minute)."""
    response = requests.get(f"https://acme-api.com/customers/{customer_id}")
    Customer.objects.update_or_create(...)

# Enqueue 1000 jobs
# They will process at 60/minute rate
for customer_id in range(1, 1001):
    sync_acme_customer.enqueue(customer_id=customer_id)
```

## Real-World Examples

### Example 1: Stripe API (100 req/s limit)

```python
@job(tags=["stripe-api"], max_retries=3)
def charge_customer(customer_id, amount):
    """Charge customer via Stripe (max 100/s)."""
    import stripe

    customer = stripe.Customer.retrieve(customer_id)
    charge = stripe.Charge.create(
        amount=amount,
        currency="usd",
        customer=customer.id
    )
    return charge.id

# Settings
TAG_CONCURRENCY_LIMITS = {"stripe-api": 5}   # 5 concurrent requests
TAG_RATE_LIMITS = {"stripe-api": "100/s"}    # 100 per second max

# Process 10,000 charges
# - Up to 5 process concurrently
# - Max 100 complete per second
# - Takes ~100 seconds minimum
```

### Example 2: Shopify API (2 req/s limit)

```python
@job(tags=["shopify-api"])
def sync_shopify_order(order_id):
    """Sync Shopify order (max 2/s)."""
    response = requests.get(
        f"https://{shop}.myshopify.com/admin/api/2023-10/orders/{order_id}.json",
        headers={"X-Shopify-Access-Token": settings.SHOPIFY_TOKEN}
    )
    Order.objects.update_or_create(...)

# Settings
TAG_CONCURRENCY_LIMITS = {"shopify-api": 2}   # 2 concurrent
TAG_RATE_LIMITS = {"shopify-api": "2/s"}      # 2 per second

# Sync 1000 orders
# - 2 concurrent requests max
# - 2 complete per second max
# - Takes ~500 seconds (8.3 minutes) minimum
for order_id in order_ids:
    sync_shopify_order.enqueue(order_id=order_id)
```

### Example 3: Slow API (1 request every 10 seconds)

```python
@job(tags=["legacy-api"])
def sync_legacy_data(record_id):
    """Sync from legacy API (max 1 per 10 seconds)."""
    # Old API that can't handle fast requests
    response = requests.get(f"https://legacy-api.com/records/{record_id}")
    LegacyRecord.objects.update_or_create(...)

# Settings
TAG_CONCURRENCY_LIMITS = {"legacy-api": 1}    # 1 concurrent
TAG_RATE_LIMITS = {"legacy-api": "1/10s"}     # 1 per 10 seconds

# Sync 100 records
# - 1 at a time
# - 1 every 10 seconds
# - Takes ~1000 seconds (16.6 minutes) minimum
```

### Example 4: Combined Tags with Different Limits

```python
@job(tags=["acme-api", "customer-sync"])
def sync_customer(customer_id):
    """Sync customer with multiple tag constraints."""
    pass

# Settings
TAG_CONCURRENCY_LIMITS = {
    "acme-api": 1,        # Acme only allows 1 concurrent
    "customer-sync": 5,   # But we can sync 5 customers at once
}

TAG_RATE_LIMITS = {
    "acme-api": "60/m",      # Acme limits to 60/minute
    "customer-sync": "500/m", # We want max 500 syncs/minute
}

# Job must satisfy ALL constraints:
# - Max 1 concurrent (acme-api limit)
# - Max 60 per minute (acme-api rate limit)
# Result: 1 at a time, 60 per minute
```

## How It Works

### Sliding Window Algorithm

1. **Job tries to claim**: Worker attempts to claim next queued job
2. **Check rate limits**: For each tag on the job:
   - Parse rate limit (e.g., "60/m" → 60 requests in 60 seconds)
   - Calculate time window (e.g., last 60 seconds)
   - Query database: Count jobs with this tag that **started** in window
   - Includes **running, successful, AND failed** jobs (all sent API requests)
   - If count >= limit: Skip job, try next
3. **Job executes**: If rate limit OK, worker claims and executes job
4. **Window slides**: Started jobs age out of window naturally

**Important**: Rate limits count when jobs **start** (send API request), not when they **finish** (receive response). This correctly matches how external APIs enforce rate limits.

**Race-Condition-Free**: Sqlery uses a small `TagLock` coordination table to eliminate race conditions. Workers acquire exclusive locks on tag rows before checking limits, ensuring truly atomic check-and-claim operations. Multiple workers will never exceed your configured limits.

### Example Timeline

```
Rate limit: "60/m" (60 per minute)

Time  | Jobs Started in Last Minute | Can Run?
------|----------------------------|----------
00:00 | 0                          | ✓ Yes
00:01 | 1                          | ✓ Yes
00:30 | 30                         | ✓ Yes
00:59 | 59                         | ✓ Yes
01:00 | 60                         | ✗ NO (limit reached)
01:01 | 60 (job from 00:00 aged out) | ✓ Yes (slot opened)
01:02 | 60                         | ✓ Yes
```

**Note**: Counts include all jobs that started API requests (running, successful, or failed), not just completed jobs.

### TagLock Coordination Table

To eliminate race conditions, Sqlery uses a small `TagLock` table that contains one row per tag:

```sql
-- Example TagLock table contents
tag
------------
acme-api
stripe-api
shopify-api
```

**How it works:**
1. Worker finds a queued job with tags
2. Worker acquires exclusive locks on TagLock rows for those tags
3. Worker checks rate limits (now atomic - no other worker can check the same tag)
4. Worker claims job if limits OK, or skips if exceeded
5. Locks released at end of transaction

**Auto-population:**
- TagLock rows are auto-created when workers encounter new tags
- Call `ensure_all_configured_tags()` on worker startup to pre-populate from settings
- Minimal overhead: Just one row per unique tag (typically 10-100 rows total)

**Database compatibility:**
- ✅ Works on PostgreSQL (uses row-level SELECT FOR UPDATE)
- ✅ Works on SQLite (uses table-level locking, still prevents races)

## Best Practices

### 1. Set Conservative Limits

```python
# API limit: 100 req/s
# Set to 80-90/s to leave safety margin
"stripe-api": "90/s"

# API limit: 2 req/s
# Set to exact limit (low risk)
"shopify-api": "2/s"
```

### 2. Monitor Rate Limit Usage

```python
from sqlery.models import QueuedJob
from django.utils import timezone
from datetime import timedelta

def check_rate_limit_usage(tag, rate_limit_str):
    """Check current rate limit usage for a tag."""
    from sqlery.rate_limit_utils import parse_rate_limit

    count, time_window = parse_rate_limit(rate_limit_str)
    threshold = timezone.now() - time_window

    # Count jobs that STARTED in the time window
    # Include running, success, AND failed (all sent API requests)
    started = QueuedJob.objects.filter(
        status__in=["running", "success", "failed"],
        tags__contains=[tag],
        started_at__gte=threshold,
        started_at__isnull=False,
    ).count()

    usage_pct = (started / count) * 100
    print(f"{tag}: {started}/{count} ({usage_pct:.1f}% of rate limit)")

    return started, count

# Check usage
check_rate_limit_usage("acme-api", "60/m")
# Output: acme-api: 45/60 (75.0% of rate limit)
```

### 3. Combine with Concurrency for Optimal Throughput

```python
# Scenario: API allows 100 concurrent connections, 10,000 req/hour

TAG_CONCURRENCY_LIMITS = {
    "api": 100,  # Use all available connections
}

TAG_RATE_LIMITS = {
    "api": "10000/h",  # Stay under hourly limit
}

# Result:
# - Up to 100 jobs run concurrently
# - Max 10,000 complete per hour
# - If jobs take 1s each: ~2.8 per second sustained
# - If jobs take 0.1s each: ~10 per second burst, then throttled
```

### 4. Handle Rate Limit Violations Gracefully

If your API returns 429 (Too Many Requests), add retry logic:

```python
@job(
    tags=["acme-api"],
    max_retries=5,
    retry_backoff=2.0  # Exponential backoff
)
def sync_customer(customer_id):
    try:
        response = requests.get(f"https://acme-api.com/customers/{customer_id}")
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            # Rate limited - will retry with backoff
            raise
        # Other errors
        raise
```

## Troubleshooting

### Jobs Process Slower Than Expected

**Symptom**: Jobs taking longer than expected to complete all

**Cause**: Rate limit is working as designed

**Solution**: Check if rate limit is appropriate

```python
# If 1000 jobs with "60/m" rate limit:
# 1000 jobs ÷ 60 per minute = 16.67 minutes minimum

# To speed up:
# 1. Increase rate limit (if API allows)
TAG_RATE_LIMITS = {"acme-api": "120/m"}

# 2. Or add more concurrency (if API allows)
TAG_CONCURRENCY_LIMITS = {"acme-api": 2}
```

### Rate Limit Not Being Enforced

**Symptom**: More jobs completing than rate limit allows

**Possible Causes**:
1. Jobs have different tags
2. Rate limit not configured
3. Workers not respecting limits (check implementation)

**Solution**:
```python
# Check job tags
job = QueuedJob.objects.get(id=123)
print(job.tags)  # ['acme-api'] or []?

# Check started jobs (includes running, successful, AND failed)
from django.utils import timezone
from datetime import timedelta

recent = QueuedJob.objects.filter(
    status__in=["running", "success", "failed"],
    tags__contains=["acme-api"],
    started_at__gte=timezone.now() - timedelta(minutes=1),
    started_at__isnull=False,
).count()

print(f"Started in last minute: {recent}")
```

### Database Performance with High Job Volume

**Symptom**: Slow job claiming when many jobs in database

**Cause**: Rate limit check queries jobs by `started_at` and `status`

**Solution**: Add database index on `started_at` and `status` (included in migration 0006)

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_queuedjob_rate_limit
ON sqlery_queuedjob (started_at, status)
WHERE tags IS NOT NULL AND tags != '[]'::jsonb;
```

This partial index only includes jobs with tags, optimizing rate limit queries.

## Performance Considerations

- **Query Cost**: Each rate limit check requires one COUNT query
- **Index**: Database index on `(started_at, status)` is included in migration 0006
- **TagLock Table**: Minimal overhead (one row per tag, typically 10-100 rows total)
- **Locking**: TagLock serializes workers per tag (acceptable for rate limiting)
- **Cleanup**: Old jobs should be cleaned up regularly to maintain performance

```python
# Auto-cleanup configuration
DJANGO_SQL_JOBS = {
    "AUTO_CLEANUP_JOBS": True,
    "JOB_RETENTION": {
        "success_max_age_days": 7,  # Keep successful jobs for 7 days
    },
}
```

## Advanced: Custom Rate Limit Periods

```python
# Every 30 seconds
"api": "10/30s"  # 10 requests per 30 seconds

# Every 5 minutes
"api": "300/5m"  # 300 requests per 5 minutes

# Every 6 hours
"api": "10000/6h"  # 10,000 requests per 6 hours
```

## Summary

Rate limiting provides:
- ✅ Respects external API rate limits
- ✅ Prevents 429 (Too Many Requests) errors
- ✅ Smooth job distribution over time
- ✅ Works with concurrency limits
- ✅ **Race-condition-free** (uses TagLock coordination table)
- ✅ Database-enforced (distributed-safe)
- ✅ Sliding window algorithm
- ✅ Flexible time windows (seconds, minutes, hours)
- ✅ Per-tag configuration
- ✅ Works on PostgreSQL and SQLite

Perfect for integrating with external APIs that have strict rate limits!
