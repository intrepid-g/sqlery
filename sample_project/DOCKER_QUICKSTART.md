# Docker Quick Start - Sqlery Dashboard

Experience the **real-time dashboard** and all features of sqlery in action!

## 🚀 Quick Start (3 commands)

```bash
cd sample_project

# Build and start
docker compose up --build

# Open browser to http://localhost:8000/admin/dashboard/
# Login: admin / admin
```

That's it! The dashboard will auto-refresh every 3 seconds.

## 📊 What You'll See

### Real-Time Dashboard Features

The dashboard at http://localhost:8000/admin/dashboard/ shows:

1. **Job Counts** (updates every 3 seconds)
   - Queued jobs (blue)
   - Running jobs (orange)
   - Success count (green)
   - Failed count (red)
   - Scheduled tasks status

2. **Active Queues Table**
   - Queue name
   - Queued job count
   - Running job count
   - Real-time status badges

3. **Recent Jobs (Last 10)**
   - Job ID
   - Task name
   - Queue
   - Status with color coding
   - Creation time
   - Duration

4. **Live Updates**
   - JavaScript fetches stats every 3 seconds
   - No page refresh needed
   - Error handling with graceful degradation

### Auto-Created Scheduled Tasks

The setup automatically creates:

- **Every Minute Task** - Runs every minute for testing
- **Every 5 Minutes** - Runs every 5 minutes

These will auto-enqueue jobs that you can watch in real-time!

## 🎮 Try It Out

### 1. Watch the Dashboard Live

Open http://localhost:8000/admin/dashboard/ and watch:
- Job counts update automatically
- New jobs appear in the recent jobs table
- Queue statistics change in real-time

### 2. Manually Enqueue Jobs

Open a shell in the container:

```bash
docker compose exec web python manage.py shell
```

Then enqueue some jobs:

```python
from tasks_app.tasks import simple_task, send_email, generate_report

# Enqueue multiple jobs quickly
for i in range(10):
    simple_task.enqueue()

# Enqueue with arguments
send_email.enqueue(
    to_email='user@example.com',
    subject='Test Email',
    body='Hello from Sqlery!'
)

# High priority job
generate_report.enqueue(
    priority=100,
    report_type='weekly'
)
```

Watch the dashboard - you'll see the jobs appear and process in real-time!

### 3. Create Scheduled Tasks

Go to http://localhost:8000/admin/sqlery/scheduledtask/ and create a new task:

- **Name**: Test Every 30 Seconds
- **Task path**: `tasks_app.tasks.simple_task`
- **Cron expression**: `*/30 * * * * *` (every 30 seconds)
- **Queue**: `default`
- **Enabled**: ✓

Within 30 seconds, watch the dashboard as jobs auto-enqueue!

### 4. Test Job Arguments

```python
# In shell
from tasks_app.tasks import send_email

# Enqueue 5 emails with different parameters
for i in range(5):
    send_email.enqueue(
        to_email=f'user{i}@example.com',
        subject=f'Email #{i}',
        body=f'This is test email number {i}'
    )
```

Check the admin to see the arguments stored in each job.

### 5. Test Retry Logic

```python
# Flaky task that might fail and retry automatically
from tasks_app.tasks import flaky_task

job = flaky_task.enqueue()
print(f"Created job {job.id}")
```

If it fails, it will automatically retry up to 3 times with exponential backoff.

## 🎯 Key Features to Observe

### Subprocess Trigger Mode
- Jobs run in isolated subprocesses (memory leak prevention)
- Middleware checks every 10 seconds (configurable)
- Watch server logs to see worker spawning

### Auto-Refresh Dashboard
- Updates every 3 seconds without page reload
- Modern vanilla JavaScript (no dependencies)
- Clean, responsive UI

### Job Execution Tracking
- See exact start/finish times
- Duration tracking
- Output and error messages
- Retry attempt history

### Queue Management
- Multiple queues (default, email, reports, etc.)
- Priority ordering (higher = sooner)
- Concurrent execution by queue

## 📁 Files Created

The Docker setup includes:

```
sample_project/
├── compose.yml           # Docker Compose config (SQLite, single service)
├── Dockerfile           # Python 3.13 slim image
├── docker-entrypoint.sh # Setup script (migrations, superuser, tasks)
└── .dockerignore        # Exclude unnecessary files
```

## 🔧 Configuration

### Default Settings (in settings.py)

```python
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'subprocess',      # Isolated worker processes
    'CHECK_INTERVAL_SECONDS': 10,      # Fast for demo (use 60 in prod)
    'DEFAULT_QUEUE': 'default',
    'DEFAULT_PRIORITY': 0,
    'DEFAULT_MAX_RETRIES': 3,
    'DEFAULT_RETRY_BACKOFF': 1.0,
}
```

### Middleware

Uses `SubprocessTriggerMiddleware` (recommended) which:
- Spawns detached worker processes
- Works with WSGI and ASGI servers
- No network dependencies
- Prevents memory leaks

## 🐛 Troubleshooting

### Dashboard not updating?
```bash
# Check browser console for errors
# Verify endpoint returns JSON:
curl http://localhost:8000/admin/dashboard/stats/
```

### Jobs not processing?
```bash
# Check logs
docker compose logs -f web

# Manually run worker
docker compose exec web python manage.py run_jobs --worker-only
```

### Permission denied on entrypoint?
```bash
chmod +x docker-entrypoint.sh
docker compose up --build
```

## 🎓 Understanding the Dashboard Implementation

The dashboard uses:

1. **Backend API** (`views.py:dashboard_stats`)
   - Returns JSON with job counts, queue stats, recent jobs
   - Efficient Django ORM queries with aggregation
   - Updates every request (stateless)

2. **Frontend JavaScript** (`dashboard.html`)
   - Vanilla JavaScript (no frameworks)
   - `setInterval()` for 3-second polling
   - Async/await for clean API calls
   - XSS protection with `escapeHtml()`
   - Graceful error handling

3. **Django Admin Integration**
   - Extends `admin/base_site.html`
   - Uses Django's admin styles
   - Staff-only access via decorator

## 📊 Sample Tasks Included

| Task | Queue | Features |
|------|-------|----------|
| `simple_task` | default | Basic demo task |
| `send_email` | email | Parallel execution, arguments |
| `generate_report` | reports | Priority, timeout |
| `scheduled_daily_task` | default | For cron schedules |
| `flaky_task` | default | Retry logic demo |

## 🚢 Production Considerations

Before deploying to production:

1. **Use PostgreSQL** for atomic job claiming (SKIP LOCKED)
2. **Increase check interval** to 60+ seconds
3. **Set SECRET_KEY** from environment
4. **Configure ALLOWED_HOSTS**
5. **Use gunicorn/uvicorn** instead of runserver
6. **Set up monitoring** for failed jobs

## 🧹 Cleanup

```bash
# Stop and remove containers
docker compose down

# Remove volumes (deletes database)
docker compose down -v
```

## 🎉 Next Steps

- Explore the Django admin UI
- Check out the source code in `src/sqlery/`
- Read the main README for deployment options
- Try HTTP trigger mode (ASGI) or direct middleware mode
- Integrate into your own Django project!

---

**Dashboard URL**: http://localhost:8000/admin/dashboard/
**Admin URL**: http://localhost:8000/admin/
**Login**: admin / admin

Enjoy exploring sqlery! 🚀
