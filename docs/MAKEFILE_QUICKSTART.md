# Sqlery Makefile - Quick Start

This is a quick reference for the Sqlery Makefile. For complete documentation, see [MAKEFILE_GUIDE.md](MAKEFILE_GUIDE.md).

## Interactive Menu

```bash
# Run the interactive menu (easiest way to get started)
make

# This will show a menu with 14 options:
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
```

## First-Time Setup

```bash
# Option 1: Use interactive menu
make
# Then select option 1 (Setup)

# Option 2: Direct command
make setup

# View all available commands
make help
```

## Common Commands

### Worker Management

```bash
# Start single worker (foreground)
make worker

# Start 4 workers in background
make workers-parallel NUM=4

# Start dedicated workers for each queue (high, default, low)
make workers-separate-queues

# Stop all background workers
make workers-stop

# Check worker status
make workers-status

# View all worker logs
make logs
```

### Job Management

```bash
# Enqueue demo jobs for testing
make demo-jobs

# Check job queue status
make jobs-status

# Clear queued/failed jobs
make jobs-clear
```

### Configuration

```bash
# List available configurations
make config-list

# Switch to multi-worker configuration
make config-use CONFIG=multi-worker

# Show current configuration
make config-show
```

### Development

```bash
# Start Django development server
make dev

# Open Django shell
make shell

# Run tests
make test
```

### Cleanup

```bash
# Stop workers and clean generated files
make clean

# Deep clean (venv + database)
make clean-all
```

## Quick Examples

### Example 1: Basic Workflow

```bash
make setup          # Setup
make demo-jobs      # Enqueue test jobs
make worker         # Start worker (Ctrl+C to stop)
```

### Example 2: Multiple Parallel Workers

```bash
make setup                      # Setup
make workers-parallel NUM=8     # Start 8 workers
make demo-jobs                  # Enqueue jobs
make logs                       # View logs (Ctrl+C to exit)
make workers-stop               # Stop workers
```

### Example 3: Queue-Specific Workers

```bash
make setup                      # Setup
make workers-separate-queues    # Start queue-specific workers
make demo-jobs                  # Enqueue jobs to different queues
make logs-worker-high           # View high priority queue logs
make workers-stop               # Stop all workers
```

### Example 4: Immediate/Synchronous Execution

```bash
# Run task directly without enqueueing
make run-task TASK=tasks_app.tasks.fast_task ARGS="'number':42"

# Execute a queued job synchronously
make run-job-sync JOB_ID=123

# Full demo
make test-immediate-execution
```

### Example 5: Rate Limiting

```bash
# Demo rate limiting (10 jobs per minute)
make demo-rate-limiting-full

# Or manually:
make test-rate-limiting         # Enqueue 20 jobs with rate limit tag
make worker-rate-limited        # Start worker with 10/minute limit
```

### Example 6: Concurrency Limiting

```bash
# Demo concurrency limits (max 2 concurrent jobs)
make demo-concurrency-full

# Or start 4 workers but limit to 2 concurrent 'slow-api' jobs
make workers-concurrency-limited
```

### Example 7: Job Dependencies

```bash
# Simple chain: job1 → job2 → job3
make test-job-dependencies

# Fan-out: 1 → 3 parallel jobs
make demo-dependencies-fan-out

# Fan-in: 3 parallel → 1 final job
make demo-dependencies-fan-in
```

### Example 8: Webhooks

```bash
# Demo webhook notifications
make test-webhooks              # Instructions for webhook setup
make demo-webhook-success       # Test successful job webhook
make demo-webhook-failure       # Test failed job webhook
```

### Example 9: Complete ETL Pipeline

```bash
# Full pipeline with dependencies, rate limits, and webhooks
make demo-full-pipeline
```

## Configuration Profiles

The Makefile includes 6 built-in configuration profiles:

| Profile | Description | Workers | Queues |
|---------|-------------|---------|--------|
| `default` | Single worker, middleware | 1 | default |
| `multi-worker` | Multiple workers, daemon | 4 | high, default, low |
| `queue-high` | High priority only | - | high |
| `queue-low` | Low priority only | - | low |
| `eventbridge` | AWS EventBridge mode | - | - |
| `http-trigger` | HTTP trigger mode | - | - |

Switch profiles with:
```bash
make config-use CONFIG=multi-worker
```

## Worker Targets Summary

| Target | Description | Use Case |
|--------|-------------|----------|
| `worker` | Single worker (foreground) | Basic processing, development |
| `worker-once` | Process jobs once and exit | Testing, debugging |
| `worker-queue QUEUE=high` | Specific queue only | Queue isolation |
| `workers-parallel NUM=4` | 4 parallel workers | High throughput |
| `workers-separate-queues` | One worker per queue | Priority enforcement |
| `workers-multi-queue` | 2 workers per queue | Balanced throughput |
| `workers-stop` | Stop all workers | Cleanup |
| `workers-status` | Check worker status | Monitoring |

## Testing & Demo Targets

| Category | Target | Description |
|----------|--------|-------------|
| **Immediate Execution** | `run-task` | Run task without enqueueing |
| | `run-job-sync` | Execute queued job synchronously |
| | `test-immediate-execution` | Full demo |
| **Rate Limiting** | `test-rate-limiting` | Enqueue jobs with rate limits |
| | `worker-rate-limited` | Worker with rate limit |
| | `demo-rate-limiting-full` | Complete demo |
| **Concurrency** | `test-concurrency-limits` | Enqueue with concurrency limits |
| | `workers-concurrency-limited` | Workers with limits |
| | `demo-concurrency-full` | Complete demo |
| **Webhooks** | `test-webhooks` | Webhook setup instructions |
| | `demo-webhook-success` | Test success webhook |
| | `demo-webhook-failure` | Test failure webhook |
| **Dependencies** | `test-job-dependencies` | Simple job chain |
| | `demo-dependencies-fan-out` | Fan-out pattern (1→many) |
| | `demo-dependencies-fan-in` | Fan-in pattern (many→1) |
| **Advanced** | `demo-full-pipeline` | Complete ETL pipeline |

## Log Management

Logs are stored in `.makefile-logs/`:

```bash
# View all logs
make logs

# View specific queue logs
make logs-worker-high
make logs-worker-default
make logs-worker-low

# View individual worker logs
tail -f .makefile-logs/worker-1.log
```

## Troubleshooting

### Workers won't start
```bash
make install        # Reinstall dependencies
make db-migrate     # Run migrations
```

### Workers not processing jobs
```bash
make workers-status # Check if workers are running
make jobs-status    # Check job queue
make logs           # View logs for errors
```

### Clean up stuck workers
```bash
make workers-stop   # Stop all workers
make clean          # Clean up PIDs and logs
```

### Database issues
```bash
make db-reset       # Reset database (WARNING: deletes data)
```

## Advanced Usage

### Custom Worker Configuration

```bash
# Start worker with custom settings
SQLERY_WORKER_QUEUES=custom \
SQLERY_TAG_CONCURRENCY_LIMITS='{"api": 5}' \
make worker
```

### Custom Configuration Profile

```bash
# Create custom config
cat > .makefile-configs/my-config.env << EOF
SQLERY_MAX_WORKERS=2
SQLERY_WORKER_QUEUES=custom-queue
EOF

# Use custom config
make config-use CONFIG=my-config
```

### Docker Deployment

```bash
make docker-build   # Build image
make docker-up      # Start stack
make docker-logs    # View logs
make docker-down    # Stop stack
```

## Tips

1. **Use `make help`** - Shows all available commands
2. **Check logs** - `make logs` shows real-time worker activity
3. **Monitor status** - `make workers-status` and `make jobs-status`
4. **Use configs** - Switch profiles with `make config-use`
5. **Clean up** - Always run `make workers-stop` before exiting

## Resources

- **Complete Guide**: [MAKEFILE_GUIDE.md](MAKEFILE_GUIDE.md)
- **Sqlery Docs**: [README.md](README.md)
- **Configuration**: [CONFIGURATION.md](CONFIGURATION.md)

## Summary

```bash
# Quick workflow
make setup                     # 1. First-time setup
make config-use CONFIG=...     # 2. Choose configuration
make workers-parallel NUM=4    # 3. Start workers
make demo-jobs                 # 4. Enqueue jobs
make logs                      # 5. Monitor
make workers-stop              # 6. Stop workers
make clean                     # 7. Cleanup
```

For detailed documentation and advanced usage, see **[MAKEFILE_GUIDE.md](MAKEFILE_GUIDE.md)**.
