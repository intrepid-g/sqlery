# Database Examples - Viewing Jobs in Sqlery

This guide shows how to populate the database with sample jobs and view them.

## Quick Start

```bash
# 1. Populate database with sample jobs
make populate-db

# 2. View what's in the database
make jobs-list

# 3. Check status summary
make jobs-status

# 4. View specific job details
make jobs-view JOB_ID=1
```

## Database Population Targets

### `make populate-db` - Sample Dataset

Creates **~30 diverse jobs** showcasing all features:

**What it creates:**
- ✅ 10 queued jobs (default, high, low queues)
- ✅ 5 jobs with rate limiting tags
- ✅ 3 jobs with concurrency tags
- ✅ 3 chained jobs (dependencies)
- ✅ 2 jobs with webhooks
- ✅ 4 jobs in fan-out pattern (1 → 3)

**Example output:**
```
Creating sample jobs:
  - 10 queued jobs (various queues)
  - 5 jobs with tags (rate limiting)
  - 3 jobs with concurrency tags
  - 3 chained jobs (dependencies)
  - 2 jobs with webhooks
  - Fan-out pattern (1 → 3)

✓ Created 29 queued jobs

View them:
  make jobs-list      # Detailed list
  make jobs-status    # Status summary
  make jobs-view JOB_ID=1  # View specific job
```

### `make populate-db-large` - Large Dataset

Creates **120+ jobs** for load testing:

**What it creates:**
- 50 fast jobs (default queue)
- 20 high priority jobs
- 20 low priority jobs
- 20 jobs with rate limit tags
- 10 slow jobs with concurrency limits

**Use case:** Load testing, performance testing, stress testing

```bash
make populate-db-large
make workers-parallel NUM=8  # Process with 8 workers
```

### `make populate-db-states` - Various States

Creates jobs in **different states** (queued, running, success, failed):

**What it creates:**
- 5 queued jobs
- 3 successful jobs (simulated)
- 2 failed jobs (simulated)
- 1 running job (simulated)

**Use case:** Testing job history, viewing completed jobs, admin dashboard testing

```bash
make populate-db-states
make jobs-list  # See jobs in different states
```

## Viewing Database Contents

### `make jobs-status` - Quick Summary

Shows job counts by queue and status:

```
Job Queue Status:

  default         | queued     |     5 jobs
  default         | success    |     3 jobs
  high            | queued     |     3 jobs
  low             | queued     |     2 jobs
  low             | failed     |     2 jobs

  Total: 15 jobs
```

### `make jobs-list` - Detailed List

Shows detailed table of all jobs (latest 50):

```
All Jobs (detailed):

   ID | Status     | Queue      | Task                                     | Tags                 |  Deps
--------------------------------------------------------------------------------------------------------------
    8 | queued     | default    | fast_task                                | api-test             |     0
    7 | queued     | default    | fast_task                                | api-test             |     0
    6 | queued     | high       | fast_task                                |                      |     0
    5 | success    | default    | fast_task                                |                      |     0
    4 | failed     | low        | slow_task                                |                      |     0
    3 | queued     | default    | fast_task                                |                      |     1
    2 | queued     | default    | fast_task                                |                      |     1
    1 | queued     | default    | fast_task                                |                      |     0

Showing latest 50 jobs (total: 8)
```

### `make jobs-view JOB_ID=X` - Specific Job

Shows all details for a specific job:

```bash
make jobs-view JOB_ID=3
```

**Output:**
```
Job Details:
  ID: 3
  Task: tasks_app.tasks.fast_task
  Status: queued
  Queue: default
  Priority: 0
  Created: 2024-10-22 15:30:45.123456+00:00
  Started: None
  Finished: None
  Duration: N/A
  Tags: ['api-test', 'rate-limited']
  Dependencies: [1, 2]
  Webhook: https://webhook.site/unique-id
  Webhook Status: pending
  Arguments: {
    "number": 500
  }
```

## Complete Workflow Examples

### Example 1: View Sample Dataset

```bash
# 1. Clear old data
make jobs-clear

# 2. Create sample jobs
make populate-db

# 3. View summary
make jobs-status

# 4. View detailed list
make jobs-list

# 5. Inspect specific job
make jobs-view JOB_ID=1

# 6. Process the jobs
make worker
```

### Example 2: Load Testing

```bash
# 1. Create large dataset
make populate-db-large

# 2. Check what was created
make jobs-status
# Output: ~120 queued jobs

# 3. Start multiple workers
make workers-parallel NUM=8

# 4. Monitor in real-time
watch -n 1 'make jobs-status'

# 5. View logs
make logs

# 6. Stop workers when done
make workers-stop
```

### Example 3: Explore Job States

```bash
# 1. Create jobs in various states
make populate-db-states

# 2. View all states
make jobs-list

# Output shows mix of:
#   - queued jobs
#   - running jobs
#   - successful jobs
#   - failed jobs

# 3. View successful job details
make jobs-view JOB_ID=1  # Adjust ID based on jobs-list output

# 4. View failed job details
make jobs-view JOB_ID=4  # Shows error message
```

### Example 4: Test Job Dependencies

```bash
# 1. Populate database (includes chained jobs)
make populate-db

# 2. Find chained jobs in list
make jobs-list
# Look for jobs with Deps > 0

# 3. View a job with dependencies
make jobs-view JOB_ID=3
# Shows: Dependencies: [1, 2]

# 4. Process the chain
make worker

# 5. Watch execution order
make jobs-list
# Jobs process in order: 1 → 2 → 3
```

### Example 5: Explore Database with Django Shell

```bash
# Open Django shell
make shell

# Then in shell:
>>> from sqlery.models import QueuedJob
>>>
>>> # Count jobs by status
>>> QueuedJob.objects.values('status').annotate(count=Count('id'))
>>>
>>> # Find jobs with tags
>>> jobs = QueuedJob.objects.filter(tags__contains=['api-test'])
>>> for job in jobs:
...     print(f"{job.id}: {job.task_path} - {job.tags}")
>>>
>>> # Find jobs with dependencies
>>> chained = QueuedJob.objects.exclude(dependencies=[])
>>> for job in chained:
...     print(f"{job.id} depends on: {job.dependencies}")
>>>
>>> # Find jobs with webhooks
>>> webhook_jobs = QueuedJob.objects.exclude(webhook_url=None)
>>> for job in webhook_jobs:
...     print(f"{job.id}: {job.webhook_url}")
```

### Example 6: Database Admin Interface

```bash
# 1. Start Django admin server
make dev

# 2. Open browser to http://localhost:9100/admin/

# 3. Navigate to:
#    - Sqlery > Queued Jobs
#    - View, filter, search jobs
#    - Retry failed jobs
#    - Cancel queued jobs
```

## Tips & Tricks

### Continuous Monitoring

Watch jobs change status in real-time:

```bash
# Terminal 1: Monitor status
watch -n 1 'make jobs-status'

# Terminal 2: Monitor job list
watch -n 1 'make jobs-list'

# Terminal 3: Run workers
make workers-parallel NUM=4
```

### Finding Specific Jobs

```bash
# Use Django shell for complex queries
make shell

>>> from sqlery.models import QueuedJob
>>>
>>> # Find high priority jobs
>>> QueuedJob.objects.filter(priority__gte=50)
>>>
>>> # Find slow jobs
>>> QueuedJob.objects.filter(task_path__contains='slow_task')
>>>
>>> # Find jobs in specific queue
>>> QueuedJob.objects.filter(queue_name='high', status='queued')
>>>
>>> # Find jobs with errors
>>> QueuedJob.objects.filter(status='failed').values('id', 'error')
```

### Performance Testing

```bash
# Create many jobs
make populate-db-large

# Time how fast workers process them
time make worker-once  # Process once

# Or with multiple workers
make workers-parallel NUM=8
# Monitor throughput
watch -n 1 'make jobs-status'
```

### Database Inspection

```bash
# SQLite database location
ls -lh sample_project/db.sqlite3

# Open SQLite shell
make db-shell

# Then in SQLite:
sqlite> SELECT status, COUNT(*) FROM sqlery_queuedjob GROUP BY status;
sqlite> SELECT id, task_path, status FROM sqlery_queuedjob LIMIT 10;
sqlite> .schema sqlery_queuedjob
```

## Summary of Database Commands

| Command | Description |
|---------|-------------|
| `make populate-db` | Create ~30 sample jobs (all features) |
| `make populate-db-large` | Create 120+ jobs (load testing) |
| `make populate-db-states` | Create jobs in different states |
| `make jobs-status` | Quick status summary |
| `make jobs-list` | Detailed job list (50 latest) |
| `make jobs-view JOB_ID=X` | View specific job details |
| `make jobs-clear` | Clear queued/failed jobs |
| `make shell` | Open Django shell for queries |
| `make db-shell` | Open database shell (SQLite) |
| `make dev` | Start admin interface |

## Next Steps

After populating the database:

1. **View the jobs**: `make jobs-list`
2. **Process them**: `make worker` or `make workers-parallel NUM=4`
3. **Monitor**: `make jobs-status` or `make logs`
4. **Inspect results**: `make jobs-list` (see success/failed states)
5. **View details**: `make jobs-view JOB_ID=X`

**Pro tip**: Create custom queries in Django shell for advanced filtering!
