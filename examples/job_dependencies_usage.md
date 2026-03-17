# Job Dependencies - Usage Guide

Job dependencies allow you to chain jobs together, ensuring they run in sequence and that dependent jobs only execute when their parent jobs complete successfully.

## Table of Contents

- [Quick Start](#quick-start)
- [Use Cases](#use-cases)
- [API Reference](#api-reference)
- [Examples](#examples)
- [Failure Handling](#failure-handling)
- [Best Practices](#best-practices)

## Quick Start

```python
from sqlery import enqueue

# Create a job chain using depends_on
job1 = enqueue('myapp.tasks.extract_data', source='api')
job2 = enqueue('myapp.tasks.transform_data', depends_on=[job1.id])
job3 = enqueue('myapp.tasks.load_data', depends_on=[job2.id])

# Or use the fluent API for cleaner syntax
job1 = enqueue('myapp.tasks.extract_data', source='api')
job2 = job1.then('myapp.tasks.transform_data')
job3 = job2.then('myapp.tasks.load_data')
```

## Use Cases

### ETL Pipelines

Chain extraction, transformation, and loading steps:

```python
# Extract data from multiple sources
extract_job = enqueue('etl.tasks.extract_from_api', api='stripe')

# Transform the extracted data
transform_job = extract_job.then('etl.tasks.normalize_data')

# Load into data warehouse
load_job = transform_job.then('etl.tasks.load_to_warehouse')

# Send completion notification
notify_job = load_job.then('etl.tasks.notify_completion')
```

### Video Processing Workflow

```python
# Upload video
upload_job = enqueue('video.tasks.upload_to_s3', video_path='/tmp/video.mp4')

# Transcode to multiple formats
transcode_720p = upload_job.then('video.tasks.transcode', resolution='720p')
transcode_1080p = upload_job.then('video.tasks.transcode', resolution='1080p')

# Generate thumbnails (depends on any transcode completing)
thumbnail_job = enqueue(
    'video.tasks.generate_thumbnails',
    depends_on=[transcode_720p.id, transcode_1080p.id]
)

# Notify user
notify_job = thumbnail_job.then('video.tasks.notify_user')
```

### Multi-step Data Processing

```python
# Fetch data from API
fetch_job = enqueue('data.tasks.fetch_customer_data', customer_id=123)

# Process in parallel once fetched
analyze_job = fetch_job.then('data.tasks.analyze_spending')
segment_job = fetch_job.then('data.tasks.segment_customer')

# Generate report after both complete
report_job = enqueue(
    'data.tasks.generate_report',
    depends_on=[analyze_job.id, segment_job.id]
)
```

## API Reference

### `enqueue(..., depends_on=None)`

Create a job that depends on other jobs completing successfully.

**Parameters:**
- `depends_on` (list[int], optional): List of job IDs that must complete successfully before this job runs

**Example:**
```python
parent_job = enqueue('tasks.parent_task')
child_job = enqueue('tasks.child_task', depends_on=[parent_job.id])
```

### `job.then(task_path, **kwargs)`

Fluent API for chaining jobs. Creates a new job that depends on the current job.

**Parameters:**
- `task_path` (str): Path to the task function
- `**kwargs`: Arguments to pass to the task

**Returns:**
- `QueuedJob`: The newly created dependent job

**Example:**
```python
job1 = enqueue('tasks.step1')
job2 = job1.then('tasks.step2', param='value')
job3 = job2.then('tasks.step3')
```

### `job.check_dependencies_met()`

Check if all dependencies have completed successfully.

**Returns:**
- `tuple`: `(all_met, failed_dependencies)`
  - `all_met` (bool): True if all dependencies succeeded
  - `failed_dependencies` (list): List of failed dependency job IDs

**Example:**
```python
job = QueuedJob.objects.get(id=123)
all_met, failed = job.check_dependencies_met()

if failed:
    print(f"Dependencies failed: {failed}")
elif not all_met:
    print("Dependencies still running")
else:
    print("Ready to run!")
```

## Examples

### Linear Chain

```python
# Sequential processing
step1 = enqueue('tasks.download_file', url='https://example.com/data.csv')
step2 = step1.then('tasks.parse_csv')
step3 = step2.then('tasks.validate_data')
step4 = step3.then('tasks.save_to_database')
```

### Fan-Out Pattern

One job triggers multiple parallel jobs:

```python
# Extract data once
extract = enqueue('tasks.extract_data')

# Process in parallel
process_customers = extract.then('tasks.process_customers')
process_orders = extract.then('tasks.process_orders')
process_products = extract.then('tasks.process_products')
```

### Fan-In Pattern

Multiple jobs converge into one:

```python
# Fetch from multiple sources in parallel
source1 = enqueue('tasks.fetch_from_stripe')
source2 = enqueue('tasks.fetch_from_shopify')
source3 = enqueue('tasks.fetch_from_square')

# Merge all data once fetched
merge = enqueue(
    'tasks.merge_all_data',
    depends_on=[source1.id, source2.id, source3.id]
)
```

### Complex DAG (Directed Acyclic Graph)

```python
# Root job
root = enqueue('tasks.initialize_project', project_id=42)

# First level - parallel tasks
setup_db = root.then('tasks.setup_database')
setup_cache = root.then('tasks.setup_cache')
setup_queues = root.then('tasks.setup_queues')

# Second level - depends on specific parents
load_schema = setup_db.then('tasks.load_schema')
seed_data = enqueue(
    'tasks.seed_data',
    depends_on=[load_schema.id, setup_cache.id]
)

# Final step - depends on everything
finalize = enqueue(
    'tasks.finalize_setup',
    depends_on=[seed_data.id, setup_queues.id]
)
```

### Conditional Chains

```python
# Main job
main_job = enqueue('tasks.process_payment', amount=100)

# Different paths based on logic
# Note: The tasks themselves handle the conditional logic
success_notification = main_job.then('tasks.send_success_email')
update_inventory = main_job.then('tasks.update_inventory')
```

## Failure Handling

### Automatic Failure Cascading

When a job fails, all jobs that depend on it are automatically marked as failed:

```python
job1 = enqueue('tasks.step1')  # This fails
job2 = job1.then('tasks.step2')  # Automatically marked failed
job3 = job2.then('tasks.step3')  # Also marked failed

# job2 and job3 will have:
# - status = "failed"
# - termination_reason = "dependency_failed"
# - error = "Dependency failed: job {job_id}"
```

### Checking Dependency Status

```python
from sqlery.models import QueuedJob

job = QueuedJob.objects.get(id=123)
all_met, failed_deps = job.check_dependencies_met()

if failed_deps:
    print(f"Cannot run: dependencies {failed_deps} failed")
elif not all_met:
    print("Waiting for dependencies to complete")
else:
    print("All dependencies succeeded - ready to run")
```

### Missing Dependencies

If a dependency job ID doesn't exist, the job will not run:

```python
# This job will never run (dependency 99999 doesn't exist)
job = enqueue('tasks.my_task', depends_on=[99999])

# check_dependencies_met() returns:
# (False, [99999])  # False = not ready, [99999] = missing IDs
```

## Best Practices

### 1. Keep Chains Reasonable

Don't create extremely long chains. Break them into logical groups:

```python
# ✅ Good - logical groups
batch_job = enqueue('tasks.process_batch', batch_id=1)
summary_job = batch_job.then('tasks.generate_summary')

# ❌ Avoid - overly long chain
j1 = enqueue('tasks.step1')
j2 = j1.then('tasks.step2')
j3 = j2.then('tasks.step3')
# ... 20 more steps
```

### 2. Use Fluent API for Linear Chains

```python
# ✅ Good - readable and concise
enqueue('tasks.extract') \
    .then('tasks.transform') \
    .then('tasks.load') \
    .then('tasks.notify')

# ❌ Less readable
job1 = enqueue('tasks.extract')
job2 = enqueue('tasks.transform', depends_on=[job1.id])
job3 = enqueue('tasks.load', depends_on=[job2.id])
job4 = enqueue('tasks.notify', depends_on=[job3.id])
```

### 3. Handle Failures Gracefully

Design tasks to be idempotent and handle failures:

```python
# tasks.py
def extract_data(source):
    """Extract data - can be safely retried."""
    try:
        data = fetch_from_api(source)
        save_to_temp(data)
        return {'status': 'success', 'rows': len(data)}
    except Exception as e:
        # Log error, task will fail
        logger.error(f"Extraction failed: {e}")
        raise
```

### 4. Avoid Circular Dependencies

Circular dependencies will cause deadlocks:

```python
# ❌ DON'T DO THIS
job1 = enqueue('tasks.task1', depends_on=[job2.id])  # Depends on job2
job2 = enqueue('tasks.task2', depends_on=[job1.id])  # Depends on job1
# Both jobs will never run!
```

### 5. Monitor Dependency Chains

Use Django admin or custom queries to monitor chains:

```python
from sqlery.models import QueuedJob

# Find all jobs waiting on dependencies
waiting_jobs = QueuedJob.objects.filter(
    status='queued'
).exclude(dependencies=[])

for job in waiting_jobs:
    all_met, failed = job.check_dependencies_met()
    if failed:
        print(f"Job {job.id} blocked by failed dependencies: {failed}")
```

### 6. Set Appropriate Timeouts

Jobs in a chain should have reasonable timeouts:

```python
# Each step has a timeout
extract = enqueue('tasks.extract', timeout_seconds=300)  # 5 min
transform = extract.then('tasks.transform', timeout_seconds=600)  # 10 min
load = transform.then('tasks.load', timeout_seconds=1800)  # 30 min
```

### 7. Use Priority for Critical Chains

Boost priority for important job chains:

```python
# High priority chain
critical_job = enqueue('tasks.critical_task', priority=100)
followup = critical_job.then('tasks.followup', priority=100)
```

## Implementation Details

### How It Works

1. **Job Creation**: When you enqueue a job with `depends_on`, the job IDs are stored in the `dependencies` JSONField
2. **Worker Claiming**: Before claiming a job, workers check if all dependencies have status='success'
3. **Failure Cascading**: When a job fails, the `mark_failed()` method automatically calls `fail_dependent_jobs()`
4. **Skipping**: Workers skip jobs whose dependencies are not yet complete or have failed

### Database Schema

```sql
-- QueuedJob model includes:
dependencies JSONB DEFAULT '[]'::jsonb

-- Example data:
{
  "id": 123,
  "dependencies": [120, 121],  -- Must wait for jobs 120 and 121
  "status": "queued"
}
```

### Performance Considerations

- **Dependency Checks**: O(n) where n = number of dependencies (typically < 10)
- **Failure Cascading**: O(m) where m = number of dependent jobs
- **Database Queries**: Uses efficient JSONField queries with indexes

## Troubleshooting

### Jobs Stuck in "queued" Status

**Symptom**: Jobs remain queued indefinitely

**Causes**:
1. Dependencies not yet complete
2. Dependencies failed (job should auto-fail)
3. Missing dependency IDs

**Solution**:
```python
job = QueuedJob.objects.get(id=123)
all_met, failed = job.check_dependencies_met()
print(f"Dependencies met: {all_met}, Failed: {failed}")
```

### Circular Dependencies

**Symptom**: Jobs never run

**Solution**: Review and fix the dependency graph:
```python
# Find potential cycles
for job in QueuedJob.objects.filter(status='queued'):
    if job.id in job.dependencies:
        print(f"Job {job.id} depends on itself!")
```

### Cascading Failures

**Symptom**: Many jobs failing at once

**Cause**: Root job in chain failed, cascading to all dependent jobs

**Solution**: Fix and retry the root job:
```python
root_job = QueuedJob.objects.get(id=100)
# Fix the issue, then retry
root_job.retry()
```
