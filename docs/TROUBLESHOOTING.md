# Sqlery - Troubleshooting Guide

Common issues and solutions for sqlery.

## Table of Contents

- [Jobs Not Processing](#jobs-not-processing)
- [Daemon Issues](#daemon-issues)
- [Worker Issues](#worker-issues)
- [Database Issues](#database-issues)
- [Long Transactions and Table Bloat (PostgreSQL)](#long-transactions-and-table-bloat-postgresql)
- [Performance Issues](#performance-issues)
- [Configuration Issues](#configuration-issues)

## Jobs Not Processing

### Jobs Stay in "pending" Status

**Symptoms:**
- Jobs enqueued successfully but never execute
- No workers visible in `python manage.py workers list`
- Daemon status shows "NOT running"

**Common Causes & Solutions:**

#### 1. Daemon Not Running

```bash
# Check daemon status
python manage.py daemon status

# If not running, check configuration
# settings.py
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
}

# Start daemon
python manage.py daemon start
```

#### 2. Middleware Not Configured

```python
# settings.py - Add middleware
MIDDLEWARE = [
    # ...
    'sqlery.daemon_middleware.DaemonMiddleware',  # Add this
]
```

#### 3. No HTTP Requests Triggering Middleware

Daemon spawns on first HTTP request. Make a request to trigger:

```bash
curl http://localhost:8000/
```

#### 4. Wrong Trigger Mode

```python
# Check TRIGGER_MODE setting
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',  # Must be 'daemon' for daemon mode
    # NOT 'disabled', 'middleware', 'subprocess', or 'http'
}
```

### Jobs Stuck in "running" Status

**Symptoms:**
- Jobs start but never complete
- Status stays "running" indefinitely
- Worker process may have crashed

**Solutions:**

#### 1. Worker Crashed

```bash
# Check for dead workers
python manage.py workers list

# Clean up dead workers
python manage.py workers cleanup

# Stuck jobs will be marked as failed
```

#### 2. Job Timeout

```bash
# Check if job exceeded timeout
python manage.py shell -c "
from sqlery.models import QueuedJob
job = QueuedJob.objects.get(id=YOUR_JOB_ID)
print(f'Timeout: {job.timeout_seconds}')
print(f'Duration: {(job.updated_at - job.started_at).total_seconds() if job.started_at else None}')
"
```

#### 3. Manual Recovery

```bash
# Mark stuck jobs as failed (> 1 hour old)
python manage.py shell -c "
from sqlery.models import QueuedJob
from datetime import timedelta
from django.utils import timezone

stuck = QueuedJob.objects.filter(
    status='running',
    updated_at__lt=timezone.now() - timedelta(hours=1)
)
print(f'Found {stuck.count()} stuck jobs')
stuck.update(status='failed', error='Manually marked as failed - job stuck')
"
```

## Daemon Issues

### Daemon Won't Start

**Symptoms:**
- `python manage.py daemon start` fails
- Daemon status shows "NOT running" after start
- No error message visible

**Solutions:**

#### 1. Check PID File Permissions

```bash
# Check if tmp directory exists and is writable
ls -la /path/to/your/django/project/tmp/

# Create if missing
mkdir -p /path/to/your/django/project/tmp/
chmod 755 /path/to/your/django/project/tmp/
```

#### 2. Check for Stale PID File

```bash
# Remove stale PID file
rm -f /path/to/your/django/project/tmp/sqlery_daemon.pid

# Try starting again
python manage.py daemon start
```

#### 3. Check DJANGO_SETTINGS_MODULE

```bash
# Daemon needs DJANGO_SETTINGS_MODULE in environment
export DJANGO_SETTINGS_MODULE=myproject.settings
python manage.py daemon start
```

#### 4. Check for Import Errors

```bash
# Try running daemon in foreground to see errors
python src/sqlery/daemon_worker.py
```

### Daemon Starts But No Workers Spawn

**Symptoms:**
- Daemon status shows "RUNNING"
- Worker count shows "0 / 3"
- No heartbeat file created

**Solutions:**

#### 1. Check Multi-Worker Configuration

```python
# settings.py
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 3,  # Must be > 0
}
```

#### 2. Check Worker Spawning Logs

```bash
# Enable DEBUG logging
# settings.py
LOGGING = {
    'loggers': {
        'sqlery': {
            'level': 'DEBUG',  # Enable debug logs
            'handlers': ['console'],
        },
    },
}
```

#### 3. Check for Database Connection Issues

```bash
# Test database connection
python manage.py shell -c "
from django.db import connection
connection.ensure_connection()
print('Database connection: OK')
"
```

### Worker Refuses to Start: Attempt Table Missing

**Symptoms:**
- A worker exits at start-up with `The execution attempt table 'sqlery_job_attempt' does not exist`
- A daemon keeps respawning workers that exit immediately after an upgrade

Every claim of a job records an execution attempt (`sqlery_job_attempt`); a completion is
accepted only for the attempt that is still open, so a worker that was declared dead by
recovery can no longer overwrite the outcome. Workers refuse to run without the table.

**Solution:** stop all workers (and the daemon), apply the migrations, then start them again.
Workers started before the migration do not record attempts and cannot be fenced.

```bash
# Django
python manage.py migrate sqlery

# Standalone
sqlery-migrate upgrade
```

### Worker Refuses to Start: Ready-Queue Tables Missing

**Symptoms:**
- A worker exits at start-up with `The ready-queue tables 'sqlery_job_ready' / 'sqlery_job_blocked' do not exist`
- Jobs stay `queued` after an upgrade although workers are running

Workers claim from `sqlery_job_ready`, a narrow table with one entry per claimable job, not by
scanning `sqlery_queued_job`; jobs waiting on dependencies wait in `sqlery_job_blocked` until a
dependency finishes. Workers refuse to run without the tables.

**Solution:** stop all workers (and the daemon), apply the migrations (see above), then start
them again. Workers started before the migration keep claiming from `sqlery_queued_job` and
never see the new entries. The migration gives every queued job its ready entry.

If a job stays `queued` and nothing claims it, its entry was probably lost (for example by a
raw `UPDATE sqlery_queued_job SET status = 'queued'`, which bypasses the library). The
scheduler leader repairs this on its next sweep (default every 60 seconds,
`READY_QUEUE_SWEEP_INTERVAL`); the log says `Restored N queued job(s) that had no ready entry`.

### Daemon Keeps Restarting

**Symptoms:**
- Daemon starts but crashes immediately
- Daemon status alternates between "RUNNING" and "NOT running"

**Solutions:**

#### 1. Check Application Logs

```bash
# Check for errors in Django logs
tail -f /var/log/django/app.log

# Or check syslog
journalctl -u sqlery -f
```

#### 2. Check Database Migrations

```bash
# Ensure migrations are applied
python manage.py migrate sqlery
```

#### 3. Check Memory Limits

```bash
# Check if daemon is being OOM killed
dmesg | grep -i "out of memory"
```

## Worker Issues

### Workers Not Claiming Jobs

**Symptoms:**
- Workers are idle
- Jobs stay "pending"
- Worker count shows workers active

**Solutions:**

#### 1. Check Queue Names

```python
# Ensure job queue matches worker queues
DJANGO_SQL_JOBS = {
    'WORKER_QUEUES': ['high', 'default', 'low'],  # Must include your queue
}

# Check job queue
python manage.py shell -c "
from sqlery.models import QueuedJob
for job in QueuedJob.objects.filter(status='pending')[:10]:
    print(f'Job {job.id}: queue={job.queue_name}')
"
```

#### 2. Check Allow Parallel Setting

```python
# Jobs with allow_parallel=False may be waiting
python manage.py shell -c "
from sqlery.models import QueuedJob

# Check for blocking jobs
running = QueuedJob.objects.filter(status='running', allow_parallel=False)
print(f'Blocking jobs: {running.count()}')
for job in running:
    print(f'  Queue: {job.queue_name}, Job: {job.id}')
"
```

#### 3. Check Database Locking (PostgreSQL)

```sql
-- Check for locks
SELECT pid, query, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE datname = 'your_database_name'
AND query LIKE '%sqlery%';
```

### Workers Die Unexpectedly

**Symptoms:**
- Worker count decreases over time
- Dead workers appear in `workers list`
- Jobs marked as failed with crash errors

**Solutions:**

#### 1. Check Memory Usage

```bash
# Monitor worker memory
ps aux | grep sqlery

# Add memory limits if needed
# settings.py
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 2,  # Reduce if memory constrained
}
```

#### 2. Check for Segfaults

```bash
# Check for segfaults in dmesg
dmesg | grep segfault

# Check core dumps
coredumpctl list
```

#### 3. Review Task Code

Worker crashes often caused by task code:
- Uncaught exceptions
- Memory leaks
- Infinite loops
- Blocking I/O

```python
# Add try/except to tasks
from sqlery import job
import logging

logger = logging.getLogger(__name__)

@job
def safe_task():
    try:
        # Your task logic
        pass
    except Exception as e:
        logger.exception(f"Task failed: {e}")
        raise  # Re-raise for job retry logic
```

## Database Issues

### Database Connection Errors

**Symptoms:**
- Jobs fail with database connection errors
- "too many connections" errors
- Workers can't claim jobs

**Solutions:**

#### 1. Check Connection Limits

```sql
-- PostgreSQL: Check max connections
SHOW max_connections;

-- Check current connections
SELECT count(*) FROM pg_stat_activity;
```

#### 2. Reduce Worker Count

```python
# settings.py - Reduce workers if connection limited
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 3,  # Each worker uses DB connections
}
```

#### 3. Use Connection Pooling

```python
# Install pgbouncer or use Django connection pooling
DATABASES = {
    'default': {
        # ...
        'CONN_MAX_AGE': 600,  # Connection pooling
    }
}
```

### SELECT FOR UPDATE SKIP LOCKED Not Working

**Symptoms:**
- Multiple workers process same job
- Job duplication
- "already claimed" errors

**Solutions:**

#### 1. Verify PostgreSQL

```python
# settings.py - Ensure using PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',  # NOT sqlite3 or mysql
        # ...
    }
}
```

#### 2. Check PostgreSQL Version

```sql
-- SKIP LOCKED requires PostgreSQL 9.5+
SELECT version();
```

#### 3. Fallback for Other Databases

For MySQL/SQLite (not recommended for production):

```python
# settings.py
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 1,  # Single worker only without SKIP LOCKED (default: CPU count)
}
```

### Database Growing Too Large

**Symptoms:**
- Database size exceeds expectations
- Slow queries
- Disk space issues

**Solutions:**

#### 1. Enable Auto-Cleanup

```python
# settings.py
DJANGO_SQL_JOBS = {
    'AUTO_CLEANUP_JOBS': True,
    'CLEANUP_INTERVAL_HOURS': 6,  # Cleanup every 6 hours
    'JOB_RETENTION': {
        'success_max_age_days': 1,  # Aggressive cleanup
        'success_max_count': 10000,
        'failed_max_age_days': 7,
        'failed_max_count': 5000,
    },
}
```

#### 2. Manual Cleanup

```bash
# Clean up old jobs
python manage.py cleanup_jobs auto

# VACUUM database (PostgreSQL)
python manage.py cleanup_jobs vacuum
```

#### 3. Check Table Sizes

```bash
# Get current sizes
python manage.py cleanup_jobs stats
```

## Long Transactions and Table Bloat (PostgreSQL)

`sqlery_queued_job` is updated on every job transition, so it relies on VACUUM
reclaiming dead tuples quickly. VACUUM can only remove tuples older than the
**xmin horizon**, the start of the oldest transaction or snapshot anywhere in the
**same database**, including your application's own connections. One
`idle in transaction` session, a multi-hour report query, a forgotten prepared
transaction, an abandoned replication slot or a standby with `hot_standby_feedback`
pins the horizon. Dead tuples then pile up (queue scans slow down, indexes bloat)
no matter how aggressive autovacuum is, and the damage persists after the culprit
ends until VACUUM catches up.

What sqlery does about it:

- sqlery's own worker, daemon and scheduler sessions set
  `idle_in_transaction_session_timeout` (default 60000 ms) next to `statement_timeout`
  and `lock_timeout`; job child processes (user code) are left alone. Set
  `PG_IDLE_IN_TRANSACTION_TIMEOUT_MS` to `0` to disable. Your application's other
  connections are not touched: set the same timeout there (or per role:
  `ALTER ROLE app SET idle_in_transaction_session_timeout = '60s'`).
- The health endpoints (`/_internal/health` in Django, `/health` in standalone) include an
  `xmin_horizon` block and report `"status": "degraded"` (HTTP 200) when the oldest
  xmin holder is older than `PG_XMIN_AGE_DEGRADED_SECONDS` (300), a replication slot or
  standby is more than `PG_SLOT_XID_AGE_DEGRADED` xids behind (5000000), or
  `sqlery_queued_job` has more than `PG_DEAD_TUPLE_RATIO_DEGRADED` (0.5) dead/live
  tuples with at least `PG_DEAD_TUPLE_MIN` (10000) dead. `0` disables a check. If the
  probe cannot run, the block reports `"unknown"`; health itself never fails because of it.
  The Django dashboard shows the same condition as a warning. Standalone mode reads the
  same names from `SQLERY_`-prefixed environment variables (e.g.
  `SQLERY_PG_XMIN_AGE_DEGRADED_SECONDS`).

Finding the culprit:

```sql
SELECT pid, usename, state, now() - xact_start AS xact_age, left(query, 80)
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL OR backend_xid IS NOT NULL
ORDER BY xact_start;
SELECT * FROM pg_prepared_xacts;
SELECT slot_name, active, xmin, catalog_xmin FROM pg_replication_slots;
```

Note that `pg_stat_activity` hides other roles' transaction times unless the monitoring
role is a member of `pg_read_all_stats`; the probe then under-reports.

### Drained live partitions that are not dropped

On partitioned PostgreSQL installs `sqlery_queued_job` is split by `created_at` (one hour
per partition for new partitions, `SQLERY_LIVE_PARTITION_INTERVAL`). A partition with no
row left is dropped by the daemon (or `cleanup_jobs`) once its upper bound is older than
`SQLERY_LIVE_PARTITION_GRACE` (`1 hour`), whatever `SQLERY_PARTITION_RETENTION` says; a
partition holding any row, of any status, is never dropped. The health block
`xmin_horizon.live_partitions` shows `drained_undroppable` and `oldest_drained_age_seconds`
and turns health `degraded` past `PG_LIVE_PARTITION_STUCK_DEGRADED_SECONDS` (900).

If it does, a drained partition is being held. The drop is skipped, never waited for, when:

- a transaction still has the partition locked: an old snapshot that read it (the same
  culprit as above, so end it), or an uncommitted insert with an old `created_at`;
- no daemon runs partition maintenance (`PARTITION_MAINTENANCE_ENABLED`, and a cadence
  `PARTITION_MAINTENANCE_INTERVAL_MINUTES` that is not larger than the live width);
- another maintainer holds the advisory lock.

```sql
SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound, s.n_live_tup
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE i.inhparent = 'sqlery_queued_job'::regclass ORDER BY 2;
SELECT pid, state, now() - xact_start AS age, left(query, 80)
FROM pg_stat_activity WHERE backend_xmin IS NOT NULL OR backend_xid IS NOT NULL;
```

Rows in the `sqlery_queued_job_default` partition are never dropped (a `created_at` outside
every partition, e.g. a backfill into a range that was already dropped); move or delete
them by hand. To keep the old daily shape set `SQLERY_LIVE_PARTITION_INTERVAL` to `1 day`.
Changing the interval only affects partitions created afterwards; existing ones are kept.

## Performance Issues

### Jobs Processing Too Slowly

**Symptoms:**
- Queue backlog growing
- Jobs taking too long to process
- High CPU/memory usage

**Solutions:**

#### 1. Increase Worker Count

```python
# settings.py
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 10,  # Increase workers
}
```

#### 2. Add More Nodes

Run daemon on multiple servers:

```bash
# Server 1
python manage.py daemon start

# Server 2
python manage.py daemon start

# Workers from both nodes will claim jobs
```

#### 3. Optimize Task Code

```python
# Bad: N+1 queries
@job
def process_users():
    for user in User.objects.all():
        user.profile  # N+1 query

# Good: Use select_related
@job
def process_users():
    for user in User.objects.select_related('profile').all():
        user.profile  # Single query
```

#### 4. Use Queue Priorities

```python
# Route slow jobs to separate queue
enqueue('slow.report', queue='reports', allow_parallel=False)
enqueue('fast.email', queue='email', allow_parallel=True)

# Configure priorities
DJANGO_SQL_JOBS = {
    'WORKER_QUEUES': ['email', 'reports'],  # email processed first
}
```

### High Database CPU Usage

**Symptoms:**
- Database CPU at 100%
- Slow job claiming
- Workers timeout waiting for locks

**Solutions:**

#### 1. Add Database Indexes

```bash
# Migrations should have created these, but verify:
python manage.py shell -c "
from django.db import connection
cursor = connection.cursor()

# Check indexes on QueuedJob
cursor.execute('''
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE tablename = 'sqlery_queuedjob'
''')

for row in cursor.fetchall():
    print(row)
"
```

#### 2. Reduce Polling Frequency

```python
# settings.py
DJANGO_SQL_JOBS = {
    'DAEMON_CHECK_INTERVAL': 30,  # Increase from 10 to 30 seconds
}
```

#### 3. Partition Tables (Advanced)

For very high volumes (millions of jobs), consider PostgreSQL table partitioning by date.

## Configuration Issues

### Settings Not Taking Effect

**Symptoms:**
- Changed settings but behavior unchanged
- Workers still using old configuration

**Solutions:**

#### 1. Restart Daemon

```bash
# Settings require daemon restart
python manage.py daemon restart
```

#### 2. Check Settings Module

```bash
# Verify correct settings file
python manage.py shell -c "
from django.conf import settings
print(settings.SETTINGS_MODULE)
print(settings.DJANGO_SQL_JOBS)
"
```

#### 3. Clear Cached Settings

```bash
# Clear any settings cache
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} +
```

### Import Errors for Tasks

**Symptoms:**
- Jobs fail with "Cannot import task" error
- Task path not found

**Solutions:**

#### 1. Verify Task Path

```bash
# Test import
python manage.py shell -c "
from django.utils.module_loading import import_string
task = import_string('myapp.tasks.my_task')
print(f'Task imported: {task}')
"
```

#### 2. Check PYTHONPATH

```bash
# Ensure app is in Python path
python -c "import sys; print('\n'.join(sys.path))"
```

#### 3. Use Absolute Paths

```python
# Instead of relative imports in task path
# BAD: '.tasks.my_task'
# GOOD: 'myapp.tasks.my_task'
```

## Debugging Tips

### Enable Debug Logging

```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': '/var/log/django/sql-jobs.log',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'sqlery': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',  # Very verbose
        },
    },
}
```

### Django Shell Debugging

```bash
# Inspect job details
python manage.py shell -c "
from sqlery.models import QueuedJob

job = QueuedJob.objects.get(id=123)
print(f'Status: {job.status}')
print(f'Queue: {job.queue_name}')
print(f'Task: {job.task_path}')
print(f'Args: {job.task_kwargs}')
print(f'Error: {job.error}')
print(f'Runs: {job.runs}')
"
```

### Monitor Database Queries

```python
# settings.py - Enable query logging in development
LOGGING = {
    'loggers': {
        'django.db.backends': {
            'level': 'DEBUG',
            'handlers': ['console'],
        },
    },
}
```

### Check System Resources

```bash
# CPU usage
top -p $(pgrep -f sqlery)

# Memory usage
ps aux --sort=-%mem | grep sqlery

# Disk I/O
iostat -x 1

# Network
netstat -an | grep ESTABLISHED | grep 5432  # PostgreSQL connections
```

## Getting Help

If you can't resolve your issue:

1. **Check Logs**: Always check Django logs, system logs, and database logs first
2. **Search Issues**: Check [GitHub Issues](https://github.com/intrepid-g/sqlery/issues)
3. **Minimal Reproduction**: Create minimal example that reproduces the issue
4. **Report Bug**: Open a new issue with:
   - Django version
   - Python version
   - Database and version
   - sqlery version
   - Configuration (sanitized)
   - Full error traceback
   - Steps to reproduce

## Package Split & Import Issues

### ImportError: cannot import name 'Queue'

**Symptoms:**
- `ImportError: cannot import name 'Queue' from 'sqlery.queue'`
- Other import errors after upgrading to v0.8.0+
- App won't start

**Cause:**
Package split moved Django/FastAPI code to subfolders but left stubs.

**Solution:**

Update your imports to new paths:

```python
# OLD (v0.7 and earlier)
from sqlery.models import QueuedJob
from sqlery.decorators import job
from sqlery.queue import enqueue

# NEW (v0.8+) - Django
from sqlery.django_sqlery.models import QueuedJob
from sqlery.django_sqlery.decorators import job
from sqlery.django_sqlery.queue import enqueue

# NEW (v0.8+) - FastAPI
from sqlery.fastapi_sqlery import create_app
from sqlery.fastapi_sqlery.backend import FastAPIBackend
```

**Quick Find & Replace:**

```bash
# Find all files needing updates
grep -r "from sqlery\.models import" . --include="*.py"
grep -r "from sqlery\.decorators import" . --include="*.py"

# Use your editor's find/replace:
# Find: from sqlery.models
# Replace: from sqlery.django_sqlery.models

# Find: from sqlery.decorators
# Replace: from sqlery.django_sqlery.decorators
```

**See:** [Package Split Migration Guide](PACKAGE_SPLIT_MIGRATION.md)

---

### Django App Config Error

**Symptoms:**
- `django.core.exceptions.ImproperlyConfigured: Application labels aren't unique`
- Duplicate `sqlery` in INSTALLED_APPS

**Solution:**

Update `INSTALLED_APPS` in settings.py:

```python
# settings.py

INSTALLED_APPS = [
    # ...
    # OLD (remove this)
    # 'sqlery',

    # NEW (use this)
    'sqlery.django_sqlery',  # or just 'sqlery' still works in v0.8
]
```

---

### DeprecationWarning Messages

**Symptoms:**
- Warnings about deprecated import paths
- `DeprecationWarning: Importing from sqlery.models is deprecated`

**Solution:**

These are just warnings - code still works! Update imports when convenient:

```python
# Update from:
from sqlery.models import QueuedJob  # ⚠️ Deprecated

# To:
from sqlery.django_sqlery.models import QueuedJob  # ✅ Current
```

To suppress warnings temporarily (not recommended):

```python
# settings.py
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='sqlery')
```

---

### Module Not Found After Package Split

**Symptoms:**
- `ModuleNotFoundError: No module named 'sqlery.django_sqlery'`
- App worked before upgrade

**Solutions:**

#### 1. Reinstall Package

```bash
# Uninstall and reinstall
pip uninstall sqlery
pip install sqlery==0.8.0  # or latest version

# Or with uv
uv pip install --reinstall sqlery
```

#### 2. Check Package Installation

```bash
# Verify package structure
python -c "import sqlery; print(sqlery.__file__)"
python -c "import sqlery.django_sqlery; print('Django OK')"
python -c "import sqlery.fastapi_sqlery; print('FastAPI OK')"
```

#### 3. Clear Python Cache

```bash
# Clear cached bytecode
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -name "*.pyc" -delete
```

---

## See Also

- [Package Split Migration Guide](PACKAGE_SPLIT_MIGRATION.md) - Detailed migration instructions
- [Environment Variables](ENVIRONMENT_VARIABLES.md) - Configuration via env vars
- [Configuration Guide](CONFIGURATION.md) - Complete settings reference
- [Management Commands](MANAGEMENT_COMMANDS.md) - CLI reference
- [README.md](README.md) - Usage examples
