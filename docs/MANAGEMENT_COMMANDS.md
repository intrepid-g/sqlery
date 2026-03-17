# Sqlery - Management Commands

Complete reference for all management commands in sqlery.

## Table of Contents

- [Job Processing](#job-processing)
- [Worker Management](#worker-management)
- [Daemon Management](#daemon-management)
- [Database Cleanup](#database-cleanup)
- [Diagnostics](#diagnostics)

## Job Processing

### run_jobs

Process jobs from the queue.

```bash
# Process all pending jobs once (serverless mode)
python manage.py run_jobs --once

# Process specific queue
python manage.py run_jobs --queue email --once

# Limit number of jobs processed
python manage.py run_jobs --max-jobs 100 --once

# Continuous processing (blocks)
python manage.py run_jobs

# Verbose output
python manage.py run_jobs --once -v 2
```

**Options:**
- `--once` - Process pending jobs once and exit (for serverless)
- `--queue QUEUE` - Process jobs from specific queue only
- `--max-jobs N` - Maximum jobs to process (default: 100)
- `-v {0,1,2,3}` - Verbosity level

**Use cases:**
- **AWS Lambda**: Run with `--once` on schedule (EventBridge)
- **Cloud Run**: Run with `--once` on Cloud Scheduler trigger
- **Cron job**: Run with `--once` every minute
- **Testing**: Run with `--once` to process specific queue

**Example Lambda handler:**

```python
def handler(event, context):
    from django.core.management import call_command
    call_command('run_jobs', '--once', '--max-jobs', '100')
    return {'statusCode': 200}
```

## Worker Management

### workers list

List all active workers and their status.

```bash
# List all workers
python manage.py workers list

# Example output:
# === Sqlery Workers ===
# Active Workers: 3 / 5
#   Idle: 1, Busy: 2, Dead: 0
#
# Worker ID                              | Status | Queue   | Job ID | Started
# -------------------------------------- | ------ | ------- | ------ | -------
# worker-abc123                          | busy   | high    | 42     | 2025-10-16 10:30:00
# worker-def456                          | busy   | default | 43     | 2025-10-16 10:30:05
# worker-ghi789                          | idle   | -       | -      | 2025-10-16 10:29:55
```

**Shows:**
- Total active workers vs configured max
- Worker status breakdown (idle/busy/dead)
- Worker details (ID, status, current queue/job, start time)

### workers stop

Gracefully stop all workers.

```bash
# Stop all workers
python manage.py workers stop

# Confirmation prompt:
# This will stop 3 active workers. Continue? [y/N]:
```

**Behavior:**
- Sends SIGTERM to all worker processes
- Workers finish current job before exiting
- Safe for production use

**Use cases:**
- Deployment: Stop workers before updating code
- Maintenance: Clear workers before database maintenance
- Emergency: Stop all job processing

### workers kill

Forcefully kill specific workers.

```bash
# Kill specific worker by ID
python manage.py workers kill worker-abc123

# Kill multiple workers
python manage.py workers kill worker-abc123 worker-def456

# Confirmation prompt:
# This will forcefully kill 1 worker(s). Continue? [y/N]:
```

**Behavior:**
- Sends SIGKILL to worker process
- Immediate termination (unsafe)
- Use only for stuck/hung workers

**⚠️ Warning:** Forceful kill may leave jobs in inconsistent state.

### workers cleanup

Clean up dead/stale worker records.

```bash
# Clean up dead workers
python manage.py workers cleanup

# Example output:
# Cleaned up 2 dead workers
# Marked 1 stuck job as failed
```

**Behavior:**
- Removes worker records for dead processes
- Marks jobs from dead workers as failed
- Safe to run anytime

**Use cases:**
- After crashes or OOM kills
- Periodic cleanup (daily cron)
- After forceful worker kills

## Daemon Management

### daemon start

Start the daemon worker (multi-worker mode).

```bash
# Start daemon
python manage.py daemon start

# Example output:
# Starting daemon worker...
# ✓ Daemon started with PID 12345
# ✓ Spawned 3 workers
```

**Behavior:**
- Starts background daemon process
- Spawns configured number of workers
- Monitors worker health

**Requirements:**
- `TRIGGER_MODE='daemon'`
- `ENABLE_DAEMON=True`

### daemon stop

Stop the daemon worker.

```bash
# Stop daemon
python manage.py daemon stop

# Example output:
# Stopping daemon (PID: 12345)...
# ✓ Daemon stopped
# ✓ 3 workers stopped
```

**Behavior:**
- Sends SIGTERM to daemon process
- Daemon stops all workers gracefully
- Safe for production

### daemon status

Check daemon and worker status.

```bash
# Check daemon status
python manage.py daemon status

# Example output when running:
# === Sqlery Daemon Status ===
# ✓ Daemon is RUNNING (PID: 12345)
#
# === Worker Pool Status ===
# Active Workers: 3 / 3
#   Idle: 1, Busy: 2, Dead: 0

# Example output when not running:
# === Sqlery Daemon Status ===
# ⚠ Daemon is NOT running
#
# === Worker Pool Status ===
# Active Workers: 0 / 3
#   Idle: 0, Busy: 0, Dead: 0
```

**Shows:**
- Daemon process status and PID
- Worker pool status
- Active workers count

### daemon restart

Restart the daemon worker.

```bash
# Restart daemon
python manage.py daemon restart

# Equivalent to: daemon stop && daemon start
```

**Behavior:**
- Stops daemon gracefully
- Waits for workers to finish
- Starts new daemon with fresh workers

## Database Cleanup

### cleanup_jobs

Manage database retention and cleanup.

#### Auto Cleanup

Run automatic cleanup based on configuration:

```bash
# Run automatic cleanup (respects settings)
python manage.py cleanup_jobs auto

# Dry run (preview without deleting)
python manage.py cleanup_jobs auto --dry-run

# Example output:
# === Automatic Cleanup ===
# ✓ Deleted 1,234 success jobs older than 7 days
# ✓ Deleted 567 success jobs (kept 10,000 most recent)
# ✓ Deleted 89 failed jobs older than 30 days
# ✓ Deleted 12 finished registry entries
# ✓ Deleted 45 failed registry entries
```

#### Manual Job Cleanup

Clean up jobs by age:

```bash
# Delete all jobs older than 30 days
python manage.py cleanup_jobs jobs --days 30

# Delete success jobs older than 7 days
python manage.py cleanup_jobs jobs --status success --days 7

# Delete failed jobs older than 90 days
python manage.py cleanup_jobs jobs --status failed --days 90

# Dry run
python manage.py cleanup_jobs jobs --days 30 --dry-run
```

Clean up jobs by count:

```bash
# Keep only 10,000 most recent jobs
python manage.py cleanup_jobs jobs --count 10000

# Keep only 5,000 most recent successful jobs
python manage.py cleanup_jobs jobs --status success --count 5000

# Dry run
python manage.py cleanup_jobs jobs --count 10000 --dry-run
```

#### Registry Cleanup

Clean up old registry entries:

```bash
# Clean up all registries (respects settings)
python manage.py cleanup_jobs registries

# Clean up specific registry
python manage.py cleanup_jobs registries --registry-type finished

# Clean up with custom age
python manage.py cleanup_jobs registries --registry-type failed --days 60

# Dry run
python manage.py cleanup_jobs registries --dry-run

# Example output:
# === Registry Cleanup ===
# ✓ Deleted 123 finished registry entries
```

**Registry types:**
- `finished` - Completed jobs
- `failed` - Failed jobs
- `started` - Running jobs
- `canceled` - Canceled jobs
- `scheduled` - Scheduled jobs
- `deferred` - Deferred jobs

#### Database Statistics

View database size and job counts:

```bash
# Show database statistics
python manage.py cleanup_jobs stats

# Example output:
# === Database Statistics ===
# Jobs by status:
#   success: 12,345
#   failed: 1,234
#   pending: 56
#   running: 3
#   canceled: 12
#   Total: 13,650
#
# Registries by type:
#   finished: 8,765
#   failed: 987
#   started: 3
#   canceled: 12
#   scheduled: 45
#   deferred: 0
#   Total: 9,812
#
# Table sizes: (PostgreSQL only)
#   Jobs: 125 MB
#   Registries: 23 MB
```

#### Database Vacuum

Optimize PostgreSQL tables:

```bash
# Run VACUUM ANALYZE (PostgreSQL only)
python manage.py cleanup_jobs vacuum

# Example output:
# === Database Vacuum ===
# ✓ Database tables vacuumed successfully
```

**Note:** Only works with PostgreSQL. Reclaims disk space after deletions.

### Cleanup Strategies

#### Daily Cleanup Cron

```bash
# Add to crontab
0 2 * * * cd /app && python manage.py cleanup_jobs auto
```

#### Aggressive Cleanup (High Volume)

```bash
#!/bin/bash
# cleanup-aggressive.sh - Run every 6 hours

# Clean up jobs
python manage.py cleanup_jobs jobs --status success --days 1
python manage.py cleanup_jobs jobs --status success --count 50000
python manage.py cleanup_jobs jobs --status failed --days 7

# Clean up registries
python manage.py cleanup_jobs registries

# Vacuum database
python manage.py cleanup_jobs vacuum
```

#### Safe Cleanup (Development)

```bash
#!/bin/bash
# cleanup-safe.sh - Run weekly

# Preview first
python manage.py cleanup_jobs auto --dry-run

# Ask for confirmation
read -p "Proceed with cleanup? [y/N] " -n 1 -r
if [[ $REPLY =~ ^[Yy]$ ]]; then
    python manage.py cleanup_jobs auto
fi
```

## Diagnostics

### Health Check Script

Create a health check script for monitoring:

```bash
#!/bin/bash
# healthcheck.sh

# Check daemon status
python manage.py daemon status > /dev/null 2>&1
DAEMON_STATUS=$?

# Check worker count
WORKERS=$(python manage.py workers list 2>/dev/null | grep "Active Workers" | awk '{print $3}')

# Check pending jobs
PENDING=$(python manage.py shell -c "
from sqlery.models import QueuedJob
print(QueuedJob.objects.filter(status='pending').count())
" 2>/dev/null)

# Report
echo "Daemon: $([ $DAEMON_STATUS -eq 0 ] && echo 'OK' || echo 'FAILED')"
echo "Workers: $WORKERS"
echo "Pending: $PENDING jobs"

# Exit code
[ $DAEMON_STATUS -eq 0 ] && exit 0 || exit 1
```

### Database Size Monitoring

```bash
#!/bin/bash
# monitor-db-size.sh

# Get table sizes
python manage.py cleanup_jobs stats | grep "Table sizes" -A 3

# Alert if jobs table > 1GB
SIZE=$(python manage.py cleanup_jobs stats | grep "Jobs:" | awk '{print $2}')
if [ $(echo "$SIZE" | sed 's/[^0-9]//g') -gt 1000 ]; then
    echo "ALERT: Jobs table size is $SIZE"
    # Send alert (email, Slack, PagerDuty, etc.)
fi
```

### Worker Monitoring

```bash
#!/bin/bash
# monitor-workers.sh

# Check for dead workers
DEAD=$(python manage.py workers list 2>/dev/null | grep "Dead:" | awk '{print $2}')

if [ "$DEAD" -gt 0 ]; then
    echo "WARNING: $DEAD dead workers detected"
    python manage.py workers cleanup
fi
```

## Systemd Integration

For production deployments, integrate with systemd:

### Service File

```ini
# /etc/systemd/system/sqlery.service
[Unit]
Description=Sqlery Daemon
After=network.target postgresql.service

[Service]
Type=forking
User=www-data
Group=www-data
WorkingDirectory=/opt/myapp
Environment="DJANGO_SETTINGS_MODULE=myproject.settings"
ExecStart=/opt/myapp/venv/bin/python manage.py daemon start
ExecStop=/opt/myapp/venv/bin/python manage.py daemon stop
ExecReload=/opt/myapp/venv/bin/python manage.py daemon restart
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

### Systemd Commands

```bash
# Enable and start service
sudo systemctl enable sqlery
sudo systemctl start sqlery

# Check status
sudo systemctl status sqlery

# View logs
sudo journalctl -u sqlery -f

# Restart
sudo systemctl restart sqlery

# Stop
sudo systemctl stop sqlery
```

## Docker Integration

For Docker deployments:

### Docker Compose

```yaml
# docker-compose.yml
services:
  web:
    image: myapp:latest
    command: gunicorn myproject.wsgi:application
    # ...

  worker-daemon:
    image: myapp:latest
    command: python manage.py daemon start
    restart: unless-stopped
    depends_on:
      - db
    environment:
      - DJANGO_SETTINGS_MODULE=myproject.settings
```

### Kubernetes

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: django-worker-daemon
spec:
  replicas: 1  # Single daemon per deployment
  template:
    spec:
      containers:
      - name: worker-daemon
        image: myapp:latest
        command: ["python", "manage.py", "daemon", "start"]
        env:
        - name: DJANGO_SETTINGS_MODULE
          value: myproject.settings
        - name: MAX_WORKERS_PER_NODE
          value: "5"
```

## Cron Job Examples

### Serverless Mode (No Daemon)

```bash
# /etc/cron.d/sqlery
# Run jobs every minute
* * * * * www-data cd /opt/myapp && python manage.py run_jobs --once >> /var/log/django-jobs.log 2>&1

# Cleanup daily at 2 AM
0 2 * * * www-data cd /opt/myapp && python manage.py cleanup_jobs auto >> /var/log/django-cleanup.log 2>&1
```

### Monitoring (With Daemon)

```bash
# /etc/cron.d/sqlery-monitoring
# Health check every 5 minutes
*/5 * * * * www-data cd /opt/myapp && /opt/myapp/scripts/healthcheck.sh

# Cleanup dead workers hourly
0 * * * * www-data cd /opt/myapp && python manage.py workers cleanup

# Database cleanup daily
0 2 * * * www-data cd /opt/myapp && python manage.py cleanup_jobs auto
```

## Troubleshooting Commands

### Debug Stuck Jobs

```bash
# Find jobs stuck in "running" state
python manage.py shell -c "
from sqlery.models import QueuedJob
from datetime import timedelta
from django.utils import timezone

stuck = QueuedJob.objects.filter(
    status='running',
    updated_at__lt=timezone.now() - timedelta(hours=1)
)
for job in stuck:
    print(f'Job {job.id}: {job.task_path} - stuck for {timezone.now() - job.updated_at}')
"

# Mark stuck jobs as failed
python manage.py shell -c "
from sqlery.models import QueuedJob
from datetime import timedelta
from django.utils import timezone

QueuedJob.objects.filter(
    status='running',
    updated_at__lt=timezone.now() - timedelta(hours=1)
).update(status='failed', error='Stuck job - timed out')
"
```

### Check Queue Backlog

```bash
# Count pending jobs by queue
python manage.py shell -c "
from sqlery.models import QueuedJob
from django.db.models import Count

backlog = QueuedJob.objects.filter(status='pending').values('queue_name').annotate(count=Count('id'))
for item in backlog:
    print(f\"{item['queue_name']}: {item['count']} pending\")
"
```

### Force Cleanup

```bash
# Nuclear option: delete all jobs (USE WITH CAUTION)
python manage.py shell -c "
from sqlery.models import QueuedJob, JobRegistry
print(f'Deleting {QueuedJob.objects.count()} jobs')
print(f'Deleting {JobRegistry.objects.count()} registry entries')
QueuedJob.objects.all().delete()
JobRegistry.objects.all().delete()
print('Done')
"
```

## See Also

- [Configuration Guide](CONFIGURATION.md) - Complete settings reference
- [Troubleshooting Guide](TROUBLESHOOTING.md) - Common issues and solutions
- [README.md](README.md) - API usage and examples
