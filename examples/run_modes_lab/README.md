# Sqlery Run Modes Lab

A dockerized, production-grade lab that exercises all 6 sqlery execution modes against real PostgreSQL. This lab proves that every execution mode works correctly across both Django and standalone integration modes.

## What This Lab Demonstrates

Sqlery supports multiple execution modes for processing job queues, each with different tradeoffs:

1. **Daemon mode** — Persistent daemon process that owns a worker pool, scheduler, and lease-based queue claiming
2. **Subprocess mode** — Periodic loop that forks child processes for each job (fork-per-job, like RQ)
3. **Synchronous thread mode** — Blocking in-process job execution simulating middleware-triggered execution
4. **HTTP trigger mode** — External HTTP endpoint with HMAC-signed requests to trigger job processing
5. **Lambda / serverless-simulated mode** — Direct invocation of `sqlery.lambda_handler.handler()` simulating EventBridge
6. **Async worker mode** — Async task execution via django-tasks integration
7. **Standalone integration** — Non-Django (FastAPI + SQLModel) execution against the same PostgreSQL database

All modes are tested here against **real PostgreSQL** running in a container. This lab verifies schema compatibility, execution correctness, and cross-integration consistency.

## Quickstart

### Setup

```bash
# Copy environment variables (safe defaults included)
cp .env.example .env

# Start all services (builds images, creates database, runs migrations)
make up

# Run the verification suite (enqueues 1 job per mode, polls for completion)
make verify

# Clean up when done (stops services and removes volumes)
make down
```

The lab will automatically:
1. Build Docker images for Django and standalone modes
2. Start PostgreSQL and run schema migrations
3. Start all 11 services (each implementing one execution mode variant)
4. Print dashboard URLs for inspection

## Services and Execution Modes

### Daemon Mode (`daemon` service)

**Service**: `daemon`  
**Command**: `python manage.py daemon start --no-detach`  
**How it works**: Runs the DaemonManager process which owns a worker pool, schedules due tasks, and uses database-backed leases to coordinate job claiming. This is the "production-ready" mode for managed deployments.

Watch it work:
```bash
docker compose logs -f daemon
```

Look for heartbeat signals (SIGUSR1), worker process spawning, and job status transitions.

### Subprocess Mode (`subprocess-worker` service)

**Service**: `subprocess-worker`  
**Command**: `python /app/scripts/run_subprocess_loop.py`  
**How it works**: A periodic loop (ticking every `LAB_TICK_SECONDS`, default 5s) that calls `sqlery.triggers.trigger_queue_workers()`. Each tick spawns one subprocess worker via `python -m sqlery.core.worker_runner`.

Watch it work:
```bash
docker compose logs -f subprocess-worker
```

You'll see periodic "tick" messages and subprocess invocations.

### Synchronous Thread Mode (`thread-worker` service)

**Service**: `thread-worker`  
**Command**: `python /app/scripts/run_thread_loop.py`  
**How it works**: A periodic loop that calls `sqlery.triggers.trigger_queue_workers()` directly (blocking, in-process). Simulates middleware-triggered execution where job processing blocks the request handler.

Watch it work:
```bash
docker compose logs -f thread-worker
```

You'll see jobs being executed synchronously within the loop's thread.

### HTTP Trigger Mode (two services)

**Server**: `http-trigger` service  
**Client**: `http-caller` service

**How it works**: The `http-trigger` service runs a Django development server with a signed HTTP endpoint at `POST /internal/trigger/`. The `http-caller` service is a periodic loop that computes HMAC-SHA256 signatures and POSTs signed requests. Each request triggers one worker subprocess.

Watch the server:
```bash
docker compose logs -f http-trigger
```

Watch the client:
```bash
docker compose logs -f http-caller
```

Signatures are verified using a shared secret (`INTERNAL_SECRET` environment variable). The signing scheme is HMAC-SHA256 with 5-second timestamp expiry for replay protection.

### Lambda / Serverless-Simulated Mode (`lambda-sim` service)

**Service**: `lambda-sim`  
**Command**: `python /app/scripts/run_lambda_sim.py`  
**How it works**: A periodic loop that directly invokes `sqlery.lambda_handler.handler({"action": "poll_and_process"}, None)` without any AWS Lambda runtime or EventBridge infrastructure. This validates the handler's business logic but does NOT test actual Lambda/AWS integration.

**Limitation**: This is a local simulation only. The handler does not invoke itself recursively (no real Lambda ARN to invoke), and there are no real AWS credentials or EventBridge rules. Self-invoke logic will log a message but does nothing. Use this mode to validate that your handler code works; for production AWS integration, test in your actual AWS environment or with LocalStack/SAM.

Watch it work:
```bash
docker compose logs -f lambda-sim
```

### Async Worker Mode (`async-worker` service)

**Service**: `async-worker`  
**Command**: `python manage.py db_worker`  
**How it works**: Uses django-tasks' `db_worker` management command to process jobs asynchronously. Jobs are executed via the django-tasks async execution backend, not the default task queue mechanisms.

Watch it work:
```bash
docker compose logs -f async-worker
```

### Standalone Integration (`standalone-web` and `standalone-worker` services)

**Dashboard**: `standalone-web` service  
**Worker**: `standalone-worker` service

**How it works**: The standalone mode uses FastAPI + SQLModel (not Django ORM). Both services connect to the same PostgreSQL database using the same `sqlery_queued_job` schema. This proves schema compatibility and demonstrates that sqlery works outside Django.

- Dashboard: `http://localhost:8010`
- Worker polls the `standalone_queue` and executes jobs

Watch the dashboard:
```bash
docker compose logs -f standalone-web
```

Watch the worker:
```bash
docker compose logs -f standalone-worker
```

## Verifying All Modes

Run the verification suite:

```bash
make verify
```

This script:
1. Enqueues one test job onto each of the 7 queues (daemon, subprocess, thread, http, lambda, async, standalone)
2. Polls the PostgreSQL `sqlery_queued_job` table directly (works for both Django and standalone)
3. Waits up to `LAB_VERIFY_TIMEOUT_SECONDS` (default 60) for each job to complete
4. Prints a summary table showing PASS/FAIL per mode
5. Exits non-zero (failure) if any mode did not complete its job

All modes must pass for the lab to be considered successful.

## Dashboards and Inspection

### Standalone FastAPI Dashboard
Open http://localhost:8010 to see:
- Real-time job queue statistics
- Worker status and activity
- Scheduled task management
- Recent job runs

### Django Admin (via http-trigger service)
The `http-trigger` service runs Django's development server and includes the admin interface. You can visit `http://localhost:8001/admin` to inspect Django models (requires Django superuser setup — not configured in this lab by default).

### Direct Database Access

Query the database directly:
```bash
make psql
```

Then inspect the shared schema:
```sql
-- See all queued jobs across all modes
SELECT id, queue_name, status, created_at FROM sqlery_queued_job ORDER BY created_at DESC LIMIT 20;

-- See job details and errors
SELECT id, queue_name, task_path, status, error_message FROM sqlery_queued_job WHERE status = 'failed';

-- See scheduled tasks
SELECT id, name, queue_name, schedule_type, enabled FROM sqlery_scheduled_task;

-- See workers (daemon mode only)
SELECT id, friendly_name, status, pid, last_heartbeat FROM sqlery_worker;
```

## Environment Variables

All configurable via `.env`:

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — PostgreSQL credentials
- `DATABASE_URL` — Django connection string
- `SECRET_KEY` — Django secret (dev only, change in production)
- `INTERNAL_SECRET` — Shared secret for HTTP trigger signing
- `SQLERY_DATABASE_URL` — Standalone mode connection string
- `LAB_TICK_SECONDS` — Interval for periodic loops (default 5)
- `LAB_VERIFY_TIMEOUT_SECONDS` — Timeout for verification polling (default 60)

## Troubleshooting

### Services fail to start or keep restarting

**Check 1: Database not ready**
```bash
docker compose logs postgres
```
Wait for `ready to accept connections` message and all services to stabilize.

**Check 2: Migrations failed**
```bash
docker compose logs migrate
```
If migrations error, the database may be corrupted. Run `make clean` and `make up` to rebuild.

**Check 3: Individual service logs**
```bash
docker compose logs <service-name>
```

### Verification fails (one or more modes show FAIL)

1. Check the service logs for that mode — look for errors or exceptions
2. Query the database to see the job status:
   ```bash
   make psql
   # Then: SELECT * FROM sqlery_queued_job WHERE queue_name = '<mode_queue>' ORDER BY created_at DESC LIMIT 5;
   ```
3. If the job shows `status='failed'`, check the `error_message` field
4. Review the mode-specific section above to understand how that mode works
5. Increase `LAB_VERIFY_TIMEOUT_SECONDS` if the issue is timing (jobs taking longer than expected)

### Port conflicts

If port 5432, 8001, or 8010 are already in use on your machine:

**Option 1**: Stop the conflicting service and retry
**Option 2**: Edit `compose.yml` to use different host ports (e.g., `8011:8001` for http-trigger)
**Option 3**: Use `docker compose` service networking — services reference each other by name internally, so port conflicts only matter for localhost access

### Shell access to a container

```bash
docker compose exec <service-name> sh
```

Examples:
```bash
docker compose exec http-trigger sh
docker compose exec standalone-web sh
docker compose exec postgres bash
```

### Cleaning up and starting fresh

```bash
make clean   # Removes volumes, stops services
make up      # Rebuild everything and restart
make verify  # Test all modes again
```

## Architecture

The lab consists of:

- **PostgreSQL** (single shared database for all modes)
- **Django services** (6 variants of the same Django project with different env vars)
- **Standalone services** (FastAPI + SQLModel pointing to same database)
- **Verification script** (queries the shared schema to validate all modes completed)

All services connect to a single PostgreSQL instance and write to the same `sqlery_queued_job` table, proving schema compatibility and data sharing between integration modes.

## Performance Notes

- This lab is designed for demonstration and testing, not production
- Job execution is intentionally slow (jobs sleep for a few seconds) to make the lab observable
- The verification script polls with a 1-second interval; adjust `LAB_VERIFY_TIMEOUT_SECONDS` for slower systems
- On resource-constrained machines, services may take longer to start; increase any `depends_on` timeouts if needed

## Further Reading

- **sqlery documentation**: See `CLAUDE.md` and `README.md` in the repo root
- **Django integration**: See `src/sqlery/django_sqlery/`
- **Standalone integration**: See `src/sqlery/fastapi_sqlery/`
- **Execution modes**: See `src/sqlery/core/worker.py`, `src/sqlery/core/daemon.py`, `src/sqlery/lambda_handler.py`
