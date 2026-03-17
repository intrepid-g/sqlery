# Sqlery - Sample Project

This is a complete working example of sqlery that you can use to test all features before packaging.

## ⚡ Quick Commands

```bash
# Setup (first time only)
./setup.sh
source venv/bin/activate
python manage.py createsuperuser

# Start server
python manage.py runserver

# Manual worker (processes one job)
python manage.py run_jobs --worker-only

# View dashboard
open http://127.0.0.1:9100/admin/sqlery/dashboard/
```

## Quick Start

### Option A: Automated Setup (Linux/Mac)

```bash
cd sample_project

# Run the setup script
./setup.sh

# Activate the virtual environment
source venv/bin/activate

# Create superuser
python manage.py createsuperuser
```

### Option B: Manual Setup

#### 1. Setup Virtual Environment

```bash
cd sample_project

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/Mac
# OR
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt
```

#### 2. Run Migrations and Create Superuser

```bash
# Run migrations
python manage.py migrate

# Create superuser for admin access
python manage.py createsuperuser
# Enter username, email, password when prompted
```

### 3. Start the Development Server

```bash
python manage.py runserver
```

### 3. Access the Dashboard

Open your browser to:
- **Admin**: http://127.0.0.1:9100/admin/
- **Dashboard**: http://127.0.0.1:9100/admin/sqlery/dashboard/

Login with the superuser credentials you created.

## Testing Features

### Test 1: Manual Job Enqueueing

Open a Python shell:

```bash
python manage.py shell
```

Try these commands:

```python
# Import tasks
from tasks_app.tasks import simple_task, send_email, generate_report

# Enqueue simple task
job1 = simple_task.enqueue()
print(f"Created job {job1.id}")

# Enqueue email task with arguments
job2 = send_email.enqueue(to_email='user@example.com', subject='Hello!')
print(f"Created job {job2.id}")

# Enqueue report with priority
job3 = generate_report.enqueue(priority=100, report_type='weekly')
print(f"Created job {job3.id}")

# Check job status
from sqlery.models import QueuedJob
jobs = QueuedJob.objects.all()
for job in jobs:
    print(f"Job {job.id}: {job.status} - {job.task_path}")
```

The middleware will automatically process these jobs within ~10 seconds (CHECK_INTERVAL_SECONDS setting).

### Test 2: Scheduled Tasks (Cron)

In Django Admin:
1. Go to **Scheduled tasks**
2. Click **Add scheduled task**
3. Fill in:
   - Name: `Daily Report`
   - Task path: `tasks_app.tasks.scheduled_daily_task`
   - Cron expression: `* * * * *` (every minute for testing)
   - Queue: `default`
   - Enabled: ✓

Wait 1 minute and check **Queued jobs** - you should see the task was auto-enqueued!

### Test 3: Real-Time Dashboard

Visit http://127.0.0.1:9100/admin/sqlery/dashboard/

You should see:
- ✅ Real-time stats updating every 3 seconds
- Job counts by status
- Queue statistics
- Recent jobs
- Scheduled task info

Enqueue some jobs and watch the dashboard update in real-time!

### Test 4: Queue-Level Concurrency

```python
# In shell
from tasks_app.tasks import send_bulk_emails, run_database_migration

# These can run in parallel (allow_parallel=True)
for i in range(5):
    send_bulk_emails.enqueue(count=20)

# These run one at a time (allow_parallel=False)
run_database_migration.enqueue(migration_name='0001_initial')
run_database_migration.enqueue(migration_name='0002_add_field')
```

Check the dashboard - email jobs run simultaneously, migration jobs run sequentially.

### Test 5: Retry Logic

```python
# Flaky task with automatic retry
from tasks_app.tasks import flaky_task

job = flaky_task.enqueue()
# Check the job in admin - if it fails, it will automatically retry 3 times
```

### Test 6: Job Timeouts

```python
# Long-running task without timeout (will complete)
from tasks_app.tasks import long_running_task
job1 = long_running_task.enqueue()

# Long-running task with short timeout (will be killed)
job2 = long_running_task.enqueue(timeout_seconds=5)
# This will fail with timeout error after 5 seconds
```

### Test 7: Manual Worker Execution

Run a worker manually (processes one job then exits):

```bash
python manage.py run_jobs --worker-only
```

Run scheduler only (enqueues due scheduled tasks):

```bash
python manage.py run_jobs --scheduler-only
```

Run both (scheduler + one job):

```bash
python manage.py run_jobs
```

### Test 8: Admin Actions

In Django Admin:
1. Go to **Queued jobs**
2. Select failed jobs
3. Use action: "Retry selected failed jobs"
4. Check that new jobs were created

Try other actions:
- Enqueue scheduled tasks now
- Enable/disable scheduled tasks
- Cancel queued jobs

### Test 9: Crash Recovery

Simulate a crashed worker:

```python
# In shell
from sqlery.models import QueuedJob
from django.utils import timezone
from datetime import timedelta

# Create a job and manually mark it as running with old timestamp
job = QueuedJob.objects.create(
    task_path='tasks_app.tasks.simple_task',
    status='running',
    started_at=timezone.now() - timedelta(hours=2),
    timeout_seconds=60,
)

# Run worker - it will detect and clean up the stale job
exit()
```

```bash
python manage.py run_jobs --worker-only
```

The stale job will be marked as failed and possibly retried.

## Sample Tasks Included

| Task | Queue | Features |
|------|-------|----------|
| `simple_task` | default | Basic task |
| `send_email` | email | Parallel, timeout, arguments |
| `generate_report` | reports | Priority, timeout |
| `cleanup_old_files` | cleanup | Parallel |
| `flaky_task` | default | Retry logic |
| `run_database_migration` | migrations | Exclusive (no parallel) |
| `scheduled_daily_task` | default | For cron schedules |
| `long_running_task` | default | Timeout testing |
| `send_bulk_emails` | email | Parallel email sending |

## Configuration

See `mysite/settings.py` for Sqlery configuration:

```python
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'subprocess',  # Recommended for production
    'CHECK_INTERVAL_SECONDS': 10,  # Check every 10 seconds
    'DEFAULT_QUEUE': 'default',
    'DEFAULT_PRIORITY': 0,
    'DEFAULT_MAX_RETRIES': 3,
    'DEFAULT_RETRY_BACKOFF': 1.0,
}
```

## Troubleshooting

### Jobs not processing automatically?
- Check that middleware is enabled in `settings.py`
- Check server logs for errors
- Try manual worker: `python manage.py run_jobs --worker-only`

### Dashboard not updating?
- Check browser console for JavaScript errors
- Verify you're logged in as admin/staff user
- Check that `/admin/sqlery/dashboard/stats/` returns JSON

### Scheduled tasks not enqueueing?
- Verify task is enabled
- Check `next_run_at` is in the past
- Run scheduler manually: `python manage.py run_jobs --scheduler-only`

## Next Steps

Once you've tested everything and it works:
1. Package the `src/sqlery` directory
2. Create `setup.py` or `pyproject.toml`
3. Upload to PyPI
4. Install in production with `pip install sqlery`

## Project Structure

```
sample_project/
├── manage.py
├── db.sqlite3 (created after migrate)
├── mysite/
│   ├── __init__.py
│   ├── settings.py (configured with sqlery)
│   ├── urls.py
│   └── wsgi.py
└── tasks_app/
    ├── __init__.py
    ├── apps.py
    ├── models.py
    └── tasks.py (sample tasks)
```

The `src/sqlery` package is imported via `sys.path` in `settings.py`.
