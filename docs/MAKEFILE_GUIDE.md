# Sqlery Makefile - Complete Guide

The Sqlery Makefile provides a comprehensive set of commands for local development, testing, and running Sqlery in various configurations.

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration Management](#configuration-management)
- [Single Worker Examples](#single-worker-examples)
- [Multiple Worker Examples](#multiple-worker-examples)
- [Testing & Development](#testing--development)
- [Docker Deployment](#docker-deployment)
- [Monitoring & Debugging](#monitoring--debugging)
- [Common Workflows](#common-workflows)

## Quick Start

### Interactive Menu (Easiest Way)

The simplest way to use the Makefile is with the interactive menu:

```bash
# Just run make without arguments
make

# You'll see a menu with 14 options:
#  1) Setup (first-time installation)
#  2) Start single worker (foreground)
#  3) Start multiple workers (background)
#  4) Stop all workers
#  5) View worker status
#  6) View jobs status
#  7) Populate database with sample jobs
#  8) View jobs list
#  9) Enqueue demo jobs
# 10) View logs
# 11) Configuration management
# 12) Show all available commands (help)
# 13) Clean up
# 14) Exit

# Simply enter a number and press Enter to execute that action
```

The interactive menu is great for:
- **First-time users** - No need to remember commands
- **Quick operations** - Browse available actions visually
- **Sub-menus** - Database population and configuration have nested options

### First-Time Setup

```bash
# Option 1: Interactive menu
make
# Then select option 1

# Option 2: Direct command
make setup

# View all available commands
make help
```

### Run Your First Worker

```bash
# 1. Enqueue some demo jobs
make demo-jobs

# 2. Start a worker to process them
make worker

# 3. Check job status
make jobs-status
```

## Configuration Management

Sqlery supports multiple configuration profiles that can be easily switched.

### Available Configurations

```bash
# List all available configurations
make config-list
```

**Built-in Configurations:**
- `default.env.example` - Single worker, middleware mode
- `multi-worker.env.example` - 4 workers, daemon mode, all queues
- `queue-high.env.example` - High priority queue only
- `queue-low.env.example` - Low priority queue only
- `eventbridge.env.example` - AWS EventBridge serverless mode
- `http-trigger.env.example` - HTTP trigger mode for ASGI

### Using Configurations

```bash
# Switch to multi-worker configuration
make config-use CONFIG=multi-worker

# Show current configuration
make config-show

# Edit a configuration
make config-edit CONFIG=default
```

### Creating Custom Configurations

```bash
# Create new configuration
cat > .makefile-configs/my-custom.env << EOF
SQLERY_TRIGGER_MODE=daemon
SQLERY_MAX_WORKERS=2
SQLERY_WORKER_QUEUES=custom-queue
SQLERY_TAG_CONCURRENCY_LIMITS={"api-calls": 5}
EOF

# Use your custom configuration
make config-use CONFIG=my-custom
```

## Single Worker Examples

### Basic Worker

```bash
# Start worker (runs continuously)
make worker

# Process jobs once and exit
make worker-once

# Process max 10 jobs then exit
make worker-max-jobs MAX=10
```

### Queue-Specific Workers

```bash
# Worker for high priority queue only
make worker-queue QUEUE=high

# Worker for low priority queue only
make worker-queue QUEUE=low

# Worker for custom queue
make worker-queue QUEUE=email-sending
```

## Multiple Worker Examples

### Parallel Workers (Same Queues)

Run multiple workers processing the same queues in parallel:

```bash
# Start 4 workers in parallel
make workers-parallel NUM=4

# Start 8 workers in parallel
make workers-parallel NUM=8

# Check worker status
make workers-status

# View logs from all workers
make logs

# Stop all workers
make workers-stop
```

**Use case:** High-throughput processing, all workers handle all queues.

### Separate Workers Per Queue

Run dedicated workers for each queue:

```bash
# Start 3 workers (one per queue: high, default, low)
make workers-separate-queues

# View logs for specific queue
make logs-worker-high      # High priority queue
make logs-worker-default   # Default queue
make logs-worker-low       # Low priority queue

# Stop all workers
make workers-stop
```

**Use case:** Queue isolation, priority enforcement, dedicated resources per queue.

### Multiple Workers Per Queue

Run 2 workers per queue (6 total):

```bash
# Start 2 workers for each queue (6 total)
make workers-multi-queue

# Check worker status
make workers-status

# Stop all workers
make workers-stop
```

**Use case:** High throughput per queue, parallel processing within queues.

## Testing & Development

### Development Server

```bash
# Start Django development server
make dev
```

### Demo Jobs

```bash
# Enqueue demo jobs for testing
make demo-jobs

# This creates:
# - 5 fast jobs (default queue)
# - 3 slow jobs (default queue, 5 seconds each)
# - 2 high-priority jobs
# - 2 low-priority jobs
```

### Job Status & Management

```bash
# Check job queue status
make jobs-status

# Clear queued/failed jobs
make jobs-clear
```

### Database Management

```bash
# Run migrations
make db-migrate

# Reset database (WARNING: deletes all data)
make db-reset

# Open database shell
make db-shell
```

### Django Shell

```bash
# Open Django shell
make shell

# Example usage in shell:
# >>> from tasks_app.tasks import slow_task
# >>> job = slow_task.enqueue(seconds=10)
# >>> job.id
```

### Running Tests

```bash
# Run test suite
make test
```

## Docker Deployment

### Build & Run

```bash
# Build Docker image
make docker-build

# Start Docker Compose stack
make docker-up

# View logs
make docker-logs

# Open shell in container
make docker-shell

# Stop stack
make docker-down
```

## Monitoring & Debugging

### Worker Status

```bash
# Check which workers are running
make workers-status

# Output shows:
#   ✓ worker-1 (PID 12345) - running
#   ✓ worker-2 (PID 12346) - running
#   ✗ worker-3 (PID 12347) - not running
```

### Viewing Logs

```bash
# Tail all worker logs
make logs

# Tail specific queue worker
make logs-worker-high
make logs-worker-default
make logs-worker-low
```

Log files are stored in `.makefile-logs/`:
- `worker-1.log`, `worker-2.log`, etc. (parallel workers)
- `worker-high.log`, `worker-default.log`, `worker-low.log` (queue-specific workers)

### Job Queue Status

```bash
# View job counts by queue and status
make jobs-status

# Output shows:
#   high            | queued     |     5 jobs
#   high            | running    |     2 jobs
#   default         | queued     |    10 jobs
#   default         | success    |   100 jobs
#   low             | failed     |     1 jobs
#
#   Total: 118 jobs
```

## Common Workflows

### Workflow 1: Basic Single Worker

**Goal:** Process jobs with a single worker

```bash
# 1. Setup
make setup

# 2. Enqueue demo jobs
make demo-jobs

# 3. Start worker (runs in foreground)
make worker

# 4. In another terminal, check status
make jobs-status
```

### Workflow 2: High-Throughput Parallel Processing

**Goal:** Process many jobs quickly with multiple workers

```bash
# 1. Setup
make setup

# 2. Start 8 parallel workers in background
make workers-parallel NUM=8

# 3. Enqueue many jobs
make demo-jobs
make demo-jobs  # Enqueue again for more jobs

# 4. Monitor progress
make workers-status  # Check worker status
make logs            # View logs
make jobs-status     # Check job counts

# 5. Stop workers when done
make workers-stop
```

### Workflow 3: Queue Separation & Priority

**Goal:** Dedicated workers for each queue with priority enforcement

```bash
# 1. Setup
make setup

# 2. Start queue-specific workers
make workers-separate-queues

# 3. Enqueue jobs to different queues
# In Django shell:
make shell
>>> from tasks_app.tasks import slow_task
>>> slow_task.enqueue(seconds=5, queue='high', priority=100)
>>> slow_task.enqueue(seconds=5, queue='default', priority=50)
>>> slow_task.enqueue(seconds=5, queue='low', priority=10)
>>> exit()

# 4. Monitor each queue separately
# Terminal 1: High priority queue
make logs-worker-high

# Terminal 2: Default queue
make logs-worker-default

# Terminal 3: Low priority queue
make logs-worker-low

# 5. Check status
make jobs-status
make workers-status

# 6. Stop workers
make workers-stop
```

### Workflow 4: Development & Testing

**Goal:** Develop and test new tasks

```bash
# 1. Setup
make setup

# 2. Start development server in background
make dev &

# 3. In another terminal, enqueue test jobs
make shell
>>> from tasks_app.tasks import my_new_task
>>> job = my_new_task.enqueue(arg1='test', arg2=123)
>>> exit()

# 4. Start worker to process
make worker

# 5. Run tests
make test

# 6. Clean up
make clean
```

### Workflow 5: Docker Development

**Goal:** Run everything in Docker

```bash
# 1. Build Docker image
make docker-build

# 2. Start stack (web + database + worker)
make docker-up

# 3. View logs
make docker-logs

# 4. Open shell in container
make docker-shell

# 5. Inside container, check status
python manage.py shell
>>> from sqlery.models import QueuedJob
>>> QueuedJob.objects.count()

# 6. Stop stack
make docker-down
```

### Workflow 6: Stress Testing & Load Testing

**Goal:** Test worker performance under load

```bash
# 1. Setup
make setup

# 2. Start maximum workers (e.g., 16)
make workers-parallel NUM=16

# 3. Enqueue many jobs (script example)
make shell
>>> from tasks_app.tasks import fast_task
>>> for i in range(1000):
...     fast_task.enqueue(number=i)
>>> exit()

# 4. Monitor throughput
watch -n 1 'make jobs-status'

# 5. Monitor system resources
htop

# 6. Tail logs for errors
make logs | grep -i error

# 7. Stop workers when done
make workers-stop
```

## Advanced Usage

### Custom Worker Configurations

You can pass environment variables directly:

```bash
# Start worker with custom concurrency limits
SQLERY_TAG_CONCURRENCY_LIMITS='{"api-calls": 10}' make worker

# Start worker with rate limits
SQLERY_TAG_RATE_LIMITS='{"stripe-api": "100/m"}' make worker

# Combine multiple settings
SQLERY_MAX_WORKERS=8 \
SQLERY_WORKER_QUEUES=high,default \
SQLERY_TAG_CONCURRENCY_LIMITS='{"api": 5}' \
make worker
```

### Background Workers with Custom Settings

```bash
# Start background worker with custom config
SQLERY_WORKER_QUEUES=email \
SQLERY_TAG_RATE_LIMITS='{"smtp": "100/m"}' \
python sample_project/manage.py run_jobs --verbosity=2 \
  > .makefile-logs/worker-email.log 2>&1 &

echo $! > .makefile-pids/worker-email.pid
```

### Process Management

The Makefile stores worker PIDs in `.makefile-pids/` and logs in `.makefile-logs/`:

```bash
# Check what's running
ls -la .makefile-pids/

# Manually stop a specific worker
kill $(cat .makefile-pids/worker-1.pid)

# View specific worker log
tail -f .makefile-logs/worker-high.log

# Archive logs before cleanup
tar -czf worker-logs-$(date +%Y%m%d).tar.gz .makefile-logs/
make clean
```

## Cleanup

```bash
# Stop workers and clean generated files
make clean

# Deep clean (venv + database)
make clean-all
```

## Troubleshooting

### Workers Won't Start

```bash
# Check Python virtual environment
ls venv/

# If missing, reinstall
make install

# Check database
make db-migrate
```

### Workers Not Processing Jobs

```bash
# 1. Check worker status
make workers-status

# 2. Check job status
make jobs-status

# 3. View logs for errors
make logs

# 4. Verify workers are running correct queues
# Check worker logs for "Listening on queues: [...]"
```

### Database Locked Errors

```bash
# SQLite database locked (multiple workers on same DB)
# Solution: Use PostgreSQL for production or limit workers

# Reset database if corrupted
make db-reset
```

### Port Already in Use

```bash
# Django dev server port conflict
# Solution: Change port in make dev target or:
python sample_project/manage.py runserver 8001
```

### Clean Up Zombie Processes

```bash
# Stop all workers forcefully
make workers-stop

# If that doesn't work:
pkill -9 -f "run_jobs"

# Clean PIDs
rm -rf .makefile-pids/
```

## Tips & Best Practices

### Performance Tips

1. **Use PostgreSQL for production** - SQLite has locking issues with many workers
2. **Monitor worker count** - Don't exceed CPU core count significantly
3. **Use queue separation** - Dedicated workers prevent queue starvation
4. **Set appropriate timeouts** - Prevent runaway jobs from blocking workers
5. **Monitor memory usage** - Workers can accumulate memory over time

### Development Tips

1. **Use `worker-once` for debugging** - Process one job and exit
2. **Check logs frequently** - `make logs` shows real-time worker activity
3. **Use demo jobs** - `make demo-jobs` creates test workload
4. **Clean between tests** - `make clean` resets worker state
5. **Use configurations** - Switch between profiles with `make config-use`

### Production Considerations

1. **Use Docker** - Containerized workers are easier to scale
2. **Set resource limits** - Configure `MAX_WORKERS` appropriately
3. **Monitor health** - Implement health checks for workers
4. **Configure retries** - Set appropriate retry limits
5. **Use webhooks** - Get notifications on job failures

## Examples by Use Case

### Use Case: API Rate Limiting

**Scenario:** You're calling external APIs with rate limits (e.g., 100 requests/minute)

```bash
# 1. Create config with rate limits
cat > .makefile-configs/api-limited.env << EOF
SQLERY_TAG_RATE_LIMITS={"stripe-api": "100/m", "github-api": "5000/h"}
SQLERY_TAG_CONCURRENCY_LIMITS={"api-calls": 10}
EOF

# 2. Use configuration
make config-use CONFIG=api-limited

# 3. Start workers
make workers-parallel NUM=4

# 4. Enqueue jobs with tags
make shell
>>> from tasks_app.tasks import api_call_task
>>> api_call_task.enqueue(url='https://api.stripe.com/...', tags=['stripe-api'])
```

### Use Case: Background Email Sending

**Scenario:** Dedicated workers for sending emails

```bash
# 1. Start dedicated email worker
make worker-queue QUEUE=email

# 2. In another terminal, enqueue emails
make shell
>>> from tasks_app.tasks import send_email
>>> send_email.enqueue(to='user@example.com', subject='Welcome', queue='email')
```

### Use Case: Video Processing Pipeline

**Scenario:** High priority for uploading, normal for transcoding, low for cleanup

```bash
# 1. Start workers for each stage
make workers-separate-queues

# 2. Create pipeline jobs
make shell
>>> from tasks_app.tasks import upload_video, transcode_video, cleanup_files
>>>
>>> # Upload (high priority)
>>> job1 = upload_video.enqueue(file='/tmp/video.mp4', queue='high', priority=100)
>>>
>>> # Transcode after upload (default priority)
>>> job2 = transcode_video.enqueue(depends_on=[job1.id], queue='default')
>>>
>>> # Cleanup after transcode (low priority)
>>> job3 = cleanup_files.enqueue(depends_on=[job2.id], queue='low', priority=-10)
```

## Summary of Key Commands

| Command | Description |
|---------|-------------|
| `make help` | Show all available commands |
| `make setup` | Complete first-time setup |
| `make worker` | Start single worker (foreground) |
| `make workers-parallel NUM=4` | Start 4 workers (background) |
| `make workers-separate-queues` | Start queue-specific workers |
| `make workers-stop` | Stop all background workers |
| `make workers-status` | Check worker status |
| `make demo-jobs` | Enqueue demo jobs for testing |
| `make jobs-status` | Check job queue status |
| `make logs` | Tail all worker logs |
| `make config-use CONFIG=name` | Switch configuration |
| `make clean` | Stop workers and clean up |

---

**Need help?** Run `make help` to see all available commands with descriptions.
