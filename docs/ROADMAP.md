# Sqlery - Implementation Roadmap

## Project Status: v0.9.0 (Serverless EventBridge Support)

### ✅ Completed Features

#### Core Models
- ✅ **ScheduledTask** - Cron-based task definitions
  - Cron expression parsing with macro support (@hourly, @daily, etc.)
  - Queue name and priority configuration
  - Enable/disable functionality
  - Automatic next_run_at calculation
- ✅ **QueuedJob** - Job queue with status tracking
  - Status flow: queued → running → success/failed
  - Queue name routing
  - Priority ordering
  - Optional scheduled task reference
  - Comprehensive timing data (created, started, finished, duration)

#### Job Enqueueing
- ✅ **Manual API** - `enqueue(task_path, queue, priority)`
  - Public API for creating jobs programmatically
  - Configurable defaults via settings
- ✅ **Scheduled API** - `enqueue_at(task_path, run_at, queue, priority)`
  - Schedule jobs for specific datetime
  - Timezone-aware (UTC)
  - Workers skip jobs not yet due
- ✅ **Scheduled Enqueueing** - Cron tasks auto-enqueue jobs when due
  - Prevents duplicate enqueueing
  - Updates next_run_at after enqueueing
  - Links jobs to scheduled tasks

#### Execution Engine
- ✅ **TaskExecutor** - Dual-mode execution
  - Scheduled task checking (finds due tasks, enqueues jobs)
  - Queue processing (executes queued jobs)
  - Concurrency control (prevents duplicate execution)
  - Error handling with full traceback capture
  - Result storage (output, errors, duration)
- ✅ **Trigger System** - Optional django-tasks integration
  - Async execution via django-tasks (if installed)
  - Graceful fallback to synchronous execution
  - Separate triggers for scheduler and workers

#### Deployment Modes
- ✅ **Traditional (Middleware)** - Request-triggered execution
  - Throttled scheduler checks (every 60s by default)
  - Throttled worker checks (every 60s by default)
  - No separate processes required
- ✅ **Serverless (Command)** - On-demand execution
  - `run_jobs` command with --once flag
  - Queue filtering (--queue)
  - Job limits (--max-jobs)
  - Scheduler-only or worker-only modes

#### Infrastructure
- ✅ **Zero Dependencies** - Only Django required
  - Vendored crontabula for cron parsing (MIT)
  - No croniter, celery, or redis needed
- ✅ **Settings** - Comprehensive configuration
  - Scheduler behavior (enable, interval)
  - Queue defaults (queue name, priority)
  - Worker behavior (max jobs, auto-trigger)
  - Django-tasks integration toggle

#### Admin Interface
- ✅ **ScheduledTask Admin** - Full queue management
  - Queue name and priority display
  - Job counts with colored status
  - Manual enqueue action
  - Enable/disable tasks
- ✅ **QueuedJob Admin** - Complete job browser
  - Filters (status, queue, priority, scheduled task)
  - Detailed execution view
  - Retry failed jobs action
  - Cancel queued jobs action

#### Testing
- ✅ **Comprehensive Test Suite** - 200+ assertions across all modules
  - **test_models.py**: Model creation, status transitions, ordering, backward compatibility
  - **test_executor.py**: Scheduler methods, worker methods, queue processing, concurrency
  - **test_queue.py**: Queue processing, failure handling, timing, integration flows
  - **test_api.py**: enqueue(), enqueue_at(), defaults, integration with executor
  - **test_utils.py**: Cron parsing, task import, validation
  - **test_middleware.py**: ScheduledTaskMiddleware, throttling, error handling
  - **test_triggers.py**: All execution modes (subprocess/django-tasks/thread), error handling
  - **test_subprocess.py**: Execution strategy selection, subprocess spawning, zombie prevention
  - **test_http_trigger.py**: HMAC signatures, async endpoints, HTTP middleware
  - **test_admin.py**: Admin actions (enqueue/enable/disable/retry/cancel), display methods

#### Memory Management
- ✅ **Subprocess Isolation** - Prevent memory leaks from middleware triggers
  - EXECUTION_MODE setting (auto/subprocess/django-tasks/thread)
  - Subprocess execution wrappers (run_scheduler_subprocess, run_worker_subprocess)
  - Auto-detection of best execution strategy
  - Proper cleanup and error handling
  - Comprehensive tests

#### HTTP Trigger (ASGI Mode)
- ✅ **Signed Internal HTTP Request** - ASGI-compatible async worker trigger
  - HMAC-SHA256 signature authentication (prevents unauthorized access)
  - Async endpoint (`/_internal/worker`) with signature verification
  - Post-response HttpTriggerMiddleware (non-blocking)
  - Subprocess spawning from async view (prevents event loop blocking)
  - Zombie process prevention via start_new_session
  - Works with uvicorn/ASGI for true async (1min+ jobs won't block other requests)
  - Settings: TRIGGER_MODE, INTERNAL_SECRET, INTERNAL_BASE_URL, SIGNATURE_MAX_AGE
  - Optional httpx dependency (pip install sqlery[http])
  - Comprehensive tests (signature generation/verification, async endpoint, middleware)

#### EventBridge Trigger (Serverless/Lambda Mode)
- ✅ **AWS EventBridge Integration** - Fully serverless, event-driven job processing
  - `TRIGGER_MODE='eventbridge'` option in settings
  - Direct Lambda invocation for immediate jobs (`enqueue()`)
  - EventBridge delayed events for scheduled jobs (`enqueue_at()`)
  - Automatic EventBridge cron rules for ScheduledTask
  - Lambda handler module for processing events
  - Auto-spawning workers for queue processing
  - Settings: EVENTBRIDGE_LAMBDA_ARN, EVENTBRIDGE_BUS_NAME, AWS_REGION
  - Optional boto3 dependency (pip install sqlery[eventbridge])
  - Complete example deployment (serverless.yml)
  - Comprehensive documentation and usage guide
  - Zero always-running processes
  - Auto-scaling with Lambda concurrency
  - Cost-effective pay-per-execution model

#### Developer Experience
- ✅ **@job Decorator** - Celery-style decorator for easy task definition
  - `@job` decorator to mark functions as enqueueable
  - `.enqueue()` method on decorated functions
  - `.delay()` method as alias to `.enqueue()`
  - `.enqueue_at(datetime)` method for scheduled execution
  - Support for queue, priority, and retry parameters
  - Automatic task_path calculation
  - Functions remain directly callable
  - Override decorator defaults per call
  - Example: `@job(queue='email', priority=10, max_retries=3) def send_email(): ...`
  - Usage: `send_email.enqueue()` or `send_email.delay()` or `send_email.enqueue_at(run_time)`
  - Comprehensive tests (40+ assertions)

#### Retry Logic
- ✅ **Automatic Retry** - Failed jobs automatically retry with exponential backoff
  - Configurable retry count (`max_retries` field, default 0)
  - Exponential backoff (`retry_backoff` multiplier, default 1.0)
  - Delay formula: `retry_backoff * (2 ^ retry_count)` seconds
  - Automatic retry job creation on failure
  - Works with enqueue(), enqueue_at(), and @job decorator
  - Settings: `DEFAULT_MAX_RETRIES`, `DEFAULT_RETRY_BACKOFF`
- ✅ **JobRun Tracking** - Complete execution history
  - Stored as JSONField (`runs`) on QueuedJob model
  - Each run records: attempt_number, started_at, finished_at, status, error, output, duration
  - Visible in admin interface (formatted table)
  - Full audit trail across retry attempts
  - History preserved across retry jobs
  - Comprehensive tests (50+ assertions)

#### Job Arguments
- ✅ **Parameterized Tasks** - Pass arguments to task functions
  - `kwargs` JSONField on QueuedJob model
  - Tasks accept keyword arguments
  - Works with enqueue(), enqueue_at(), and @job decorator
  - Automatic serialization/deserialization
  - Preserved across retries
  - Supports all JSON-serializable types (strings, numbers, lists, dicts, etc.)
  - Example: `send_email.enqueue(to_email='user@example.com', subject='Welcome')`
  - Comprehensive tests (40+ assertions in test_job_arguments.py)

#### Atomic Job Claiming
- ✅ **SELECT FOR UPDATE SKIP LOCKED** - Prevents duplicate job execution (Postgres)
  - Row-level locking with `select_for_update(skip_locked=True)`
  - Atomic claiming in `get_queued_jobs()` method
  - Transaction-based status updates in `run_queue_workers()`
  - Multiple workers claim different jobs without blocking
  - Comprehensive concurrent execution tests (test_atomic_claiming.py)
  - Documented Postgres requirement and fallback behavior

#### Atomic Scheduler Claiming
- ✅ **Atomic Task Enqueueing** - Prevents duplicate scheduled job enqueueing (Postgres)
  - Atomic task claiming in `run_due_tasks()` with `SELECT FOR UPDATE SKIP LOCKED`
  - Each scheduler instance claims different tasks atomically
  - Prevents duplicate jobs from concurrent scheduler instances
  - Job creation and `next_run_at` update within same transaction
  - Comprehensive concurrent scheduler tests (test_atomic_scheduler.py)
  - Multiple schedulers can run safely in distributed deployments

#### Robust Subprocess Resolution
- ✅ **Absolute Path Resolution** - Works regardless of current working directory
  - `get_manage_py_path()` helper computes absolute path from `settings.BASE_DIR`
  - Checks both BASE_DIR and parent directory for manage.py
  - All subprocess commands use absolute paths (views.py, subprocess_executor.py)
  - Works in Docker, systemd, cloud functions, all deployment scenarios
  - Clear error messages if manage.py not found or BASE_DIR not configured
  - Comprehensive tests for path resolution and error handling
  - Fixes production blocker for non-standard deployment structures

#### Subprocess Trigger Mode
- ✅ **Direct Subprocess Spawning** - No HTTP layer (simpler, more reliable)
  - New `SubprocessTriggerMiddleware` for fire-and-forget subprocess execution
  - `TRIGGER_MODE='subprocess'` option in settings
  - No network dependencies (no HTTP request to self)
  - Works with both WSGI and ASGI servers
  - Process isolation prevents memory leaks
  - Non-blocking (instant response to users)
  - Environment variables properly inherited
  - Zombie prevention with start_new_session=True
  - Comprehensive tests (11 test cases)
  - Recommended trigger mode for production
  - Fixes all 27 HTTP trigger mode failure scenarios

#### Code Quality
- ✅ **Architecture Documentation** - Clear design docs
- ✅ **Type Hints** - Throughout vendored crontab code
- ✅ **Logging** - Comprehensive logging at all levels

#### Local Development & Testing
- ✅ **Comprehensive Makefile System** - 90+ targets for all workflows
  - **Configuration Management** - 6 built-in profiles with easy switching
    - `default`, `multi-worker`, `queue-high`, `queue-low`, `eventbridge`, `http-trigger`
    - `make config-use CONFIG=name` to switch configurations
    - `.makefile-configs/` directory with profile files
  - **Worker Orchestration** - 15+ targets for all worker scenarios
    - Single worker (foreground, once, queue-specific, rate-limited)
    - Multiple workers (parallel, separate queues, multi-queue, concurrency-limited)
    - Worker management (start, stop, status monitoring)
  - **Comprehensive Testing Suite** - 30+ test/demo targets
    - Immediate execution (`run-task`, `run-job-sync`)
    - Rate limiting demos (`demo-rate-limiting-full`)
    - Concurrency limiting demos (`demo-concurrency-full`)
    - Webhook tests (`demo-webhook-success`, `demo-webhook-failure`)
    - Job dependencies tests (`demo-dependencies-fan-out`, `demo-dependencies-fan-in`)
    - Full pipeline demo (`demo-full-pipeline`)
  - **Database Population & Viewing** - 6 targets for inspection
    - `populate-db` - ~30 diverse sample jobs (all features)
    - `populate-db-large` - 120+ jobs for load testing
    - `populate-db-states` - Jobs in various states
    - `jobs-list` - Detailed table of all jobs
    - `jobs-view JOB_ID=X` - Specific job inspection
    - `jobs-status` - Quick summary by queue/status
  - **Documentation** - 1,800+ lines across 4 guides
    - MAKEFILE_GUIDE.md - Complete reference (1,000+ lines)
    - MAKEFILE_QUICKSTART.md - Quick reference card (300+ lines)
    - DATABASE_EXAMPLES.md - Database inspection guide (500+ lines)
    - MAKEFILE_SUMMARY.md - Feature summary
  - **Developer Experience**
    - Color-coded help system
    - Setup & installation automation
    - Docker deployment support
    - Log management and monitoring
    - Cleanup utilities

---

## ⏳ In Progress

_Nothing currently in progress_

---

## 📋 TODO (Future Enhancements)

### Phase 1: Core Improvements (Critical)

- [ ] **Bulk Operations** - Admin actions
  - Bulk retry failed jobs
  - Bulk delete old jobs
  - Bulk priority changes
- [ ] **Separate Scheduler/Worker Intervals**
  - Independent throttle settings for scheduler and workers
- [ ] **Bound Output Sizes** - Prevent database bloat from large job outputs
  - **Phase 1: Configurable Truncation**
    - Add settings: `MAX_OUTPUT_SIZE` (10KB), `MAX_ERROR_SIZE` (5KB), `MAX_TRACEBACK_SIZE` (10KB)
    - Truncate in `mark_success()` and `mark_failed()` with "... [truncated]" suffix
    - Option: `STORE_FULL_IN_RUNS` (store full version in runs history)
  - **Phase 2: Separate History Table** (optional, for full audit trail)
    - Create `JobExecutionHistory` model with foreign key to `QueuedJob`
    - Fields: `job`, `attempt_number`, `started_at`, `finished_at`, `status`, `output` (TextField), `error` (TextField), `traceback` (TextField), `duration`
    - Index on `(job_id, created_at)`
    - Keep latest attempt on `QueuedJob` (truncated), full history in separate table
    - Migration to move existing `runs` JSONField data to new table
  - Current issue: Unlimited field sizes cause row bloat, slow queries, storage waste
  - Code: `sqlery/models.py` (`mark_success`, `mark_failed`, `_record_run`)

### Phase 2: Advanced Features
- [x] **Tag-Based Concurrency Limits** - Prevent resource contention (v0.9.0)
  - **Problem**: Multiple workers can exhaust shared resources (DB connections, memory, CPU)
  - **Solution**: Tag jobs and set max concurrency per tag
  - **Example Use Cases**:
    - External API sync (e.g., `acme-api` tag with max 1 concurrent job)
    - Database connections (e.g., `legacy-db` tag with max 2 concurrent jobs)
    - Expensive operations (e.g., `image-processing` tag with max 5 concurrent jobs)
  - **Implementation**:
    - `tags` JSONField on QueuedJob model (list of strings)
    - `TAG_CONCURRENCY_LIMITS` setting: `{"acme-api": 1, "legacy-db": 2}`
    - Worker claiming checks running jobs with same tags
    - Atomic SELECT FOR UPDATE ensures concurrency limits respected
    - Works across distributed workers (database-based coordination)
  - **API Changes**:
    - `enqueue(..., tags=["acme-api", "rate-limited"])`
    - `enqueue_at(..., tags=["scheduled-sync"])`
    - `@job(tags=["api-call"])` decorator support
  - **Admin Interface**:
    - Display tags on job list
    - Filter jobs by tag
    - Show current concurrency per tag
  - **Benefits**:
    - Prevents resource exhaustion
    - No need for separate queues per API
    - Fine-grained concurrency control
    - Multiple tags per job for flexible grouping
    - Better resource management
- [x] **Rate Limiting (Throttling)** - Control job execution rate over time (v0.10.0)
  - **Problem**: External APIs have rate limits (e.g., "100 requests per minute", "1 request per second")
  - **Solution**: Tag-based rate limiting with time window enforcement + TagLock coordination table
  - **Rate Limit Format**: `"{count}/{unit}"` where unit is `s` (second), `m` (minute), `h` (hour)
  - **Example Use Cases**:
    - Stripe API: `"100/s"` (100 requests per second)
    - Acme API: `"60/m"` (60 requests per minute, or 1 per second)
    - Shopify API: `"2/s"` (2 requests per second)
    - Slow API: `"1/10s"` (1 request every 10 seconds)
  - **Difference from Concurrency Limits**:
    - **Concurrency**: "Max 2 jobs running at the same time"
    - **Rate Limiting**: "Max 60 jobs per minute" (even if they finish quickly)
    - **Combined**: "Max 2 concurrent AND max 60 per minute"
  - **Implementation**:
    - `TAG_RATE_LIMITS` setting: `{"acme-api": "60/m", "stripe-api": "100/s"}`
    - **TagLock coordination table** - Eliminates race conditions
    - Workers acquire exclusive locks on TagLock rows before checking limits
    - Uses `started_at` timestamp (when job sends API request, not when it finishes)
    - Counts running, successful, AND failed jobs (all hit the API)
    - Database query: `COUNT(*) WHERE started_at >= NOW() - interval AND status IN ('running', 'success', 'failed')`
    - **Zero race conditions** - Truly atomic check-and-claim via SELECT FOR UPDATE
    - Works on PostgreSQL and SQLite
    - Database index on (started_at, status) for performance
  - **Race-Condition-Free Design**:
    - TagLock table contains one row per tag
    - Workers use `list(TagLock.objects.select_for_update().filter(tag__in=sorted_tags))` to lock ALL tags
    - Lock order sorted to prevent deadlocks
    - Atomic check-and-claim within transaction
    - Validation prevents invalid rate limits ("0/s", "100/0s")
  - **API Changes**:
    - Jobs with tags inherit rate limits from `TAG_RATE_LIMITS` setting
    - `@job(tags=["acme-api"])` uses configured rate limit
  - **Migrations**:
    - 0006_add_rate_limit_index.py - Database index for performance
    - 0007_tag_lock_table.py - TagLock coordination table
  - **Benefits**:
    - ✅ Respects external API rate limits
    - ✅ Prevents API 429 (Too Many Requests) errors
    - ✅ Smooth job distribution over time
    - ✅ **Zero race conditions** (TagLock mechanism)
    - ✅ Works with concurrency limits for complete control
    - ✅ Works on PostgreSQL and SQLite
    - ✅ Validation prevents misconfigurations
  - **Example**:
    ```python
    # Configuration
    TAG_CONCURRENCY_LIMITS = {"acme-api": 1}  # Max 1 concurrent
    TAG_RATE_LIMITS = {"acme-api": "60/m"}    # Max 60 per minute

    # Even though jobs finish in 0.1s each:
    # - Only 1 runs at a time (concurrency limit)
    # - Max 60 start per minute (rate limit)
    # - Result: Smooth 1 job/second distribution
    ```
  - **Development Notes**:
    - Underwent two rounds of adversarial review
    - Fixed 9 critical bugs including `.exists()` only locking one row
    - Comprehensive documentation in examples/rate_limiting_usage.md
- ✅ **Job Dependencies** (v0.11.0) - Chain jobs with depends_on and .then()
  - dependencies JSONField on QueuedJob stores parent job IDs
  - Worker checks dependencies before claiming jobs
  - Automatic failure cascading to dependent jobs
  - Fluent API: job1.then(task2).then(task3)
  - Helper methods: check_dependencies_met(), fail_dependent_jobs()
  - Support for complex DAG workflows (fan-out, fan-in patterns)
  - Examples: ETL pipelines, video processing, multi-step workflows
  - Comprehensive documentation in examples/job_dependencies_usage.md
- ✅ **Webhooks** (v0.11.0) - HTTP POST notifications on job completion
  - webhook_url and webhook_events fields on QueuedJob
  - Automatic webhook sending on success/failure
  - HMAC-SHA256 signature authentication (X-Sqlery-Signature header)
  - Retry logic with exponential backoff (default: 3 retries)
  - Configurable events: ['success'], ['failure'], or both
  - Complete payload with job metadata, timing, output/error
  - Batch retry function for failed webhooks
  - Examples: payment notifications, Slack alerts, external integrations
  - Comprehensive documentation in examples/webhooks_usage.md
- [ ] **Args Support**
  - Optional `args` list alongside `kwargs` for tasks

### Phase 3: Observability
- [ ] **Metrics Export** - Prometheus/StatsD
  - Queue depth
  - Processing time
  - Success/failure rates
  - Worker utilization
- [ ] **Enhanced Logging** - Structured logs
  - JSON logging option
  - Trace IDs
  - Performance metrics
- [ ] **Admin Dashboard** - Real-time view
  - Queue statistics
  - Recent jobs
  - Failure trends

### Phase 4: Scalability
- [ ] **Distributed Locking** - Postgres advisory locks
  - Replace status-based concurrency
  - Cross-instance safety
  - Automatic cleanup
- [ ] **Worker Pools** - Multiple workers per instance
  - Configurable worker count
  - Queue assignment
  - Load balancing
- [ ] **Partitioning** - Archive old jobs
  - Time-based partitioning
  - Automatic archival
  - Query optimization

### Phase 5: Deployment Hardening
- [ ] **Lazy HTTP Client Import**
  - Import `httpx` only when TRIGGER_MODE='http'
- [ ] **Robust Subprocess Spawning**
  - Avoid reliance on working directory for `manage.py`
- [ ] **Signature Skew Tolerance**
  - Increase default `SIGNATURE_MAX_AGE` or document clock sync
- [ ] **Secure Health Endpoint**
  - Optional auth or allowlist for `/_internal/health`

---

## Design Principles

### No Separate Scheduler Process
**Current**: Scheduler is NOT a separate process. It runs:
- Via middleware (traditional deployment)
- Via management command (serverless deployment)
- Optionally async via django-tasks (if installed)

**Future**: Could add optional always-running scheduler, but NOT required.

### Postgres as Queue
- SELECT FOR UPDATE SKIP LOCKED for concurrency (future enhancement)
- Status-based filtering for MVP
- Indexes optimized for queue queries

### Django-Tasks Integration
- **Optional**: Works without it (sync execution)
- **Recommended**: Better performance with async
- **Pluggable**: Can use any backend

### LEAN Philosophy
- Minimal external dependencies
- Simple, obvious code
- Production-ready from day 1
- Easy to understand and debug

---

## Version History

### v0.9.0 (Current)
- Serverless EventBridge Support
  - **AWS EventBridge Integration**
    - New `TRIGGER_MODE='eventbridge'` for fully serverless deployments
    - Direct Lambda invocation for immediate job execution
    - EventBridge delayed events for scheduled jobs
    - Automatic EventBridge cron rules for ScheduledTask
  - **Lambda Handler Module**
    - `lambda_handler.py` - AWS Lambda entry point
    - Handles `process_queue`, `run_scheduled_task`, `poll_and_process` actions
    - Auto-spawning workers for continuous queue processing
    - Supports both specific job and queue-based execution
  - **EventBridge Trigger Module**
    - `eventbridge_trigger.py` - AWS SDK integration
    - `invoke_lambda_worker()` - Direct Lambda invocation
    - `schedule_eventbridge_event()` - Delayed job scheduling
    - `ensure_cron_eventbridge_rule()` - Cron rule management
    - `delete_eventbridge_rule()` - Cleanup for one-time events
    - `disable_cron_eventbridge_rule()` - Disable cron tasks
  - **API Integration**
    - `enqueue()` modified to invoke Lambda when TRIGGER_MODE='eventbridge'
    - `enqueue_at()` modified to schedule EventBridge delayed events
    - Automatic EventBridge rule creation when ScheduledTask saved
  - **Configuration**
    - EVENTBRIDGE_LAMBDA_ARN - Lambda function ARN
    - EVENTBRIDGE_BUS_NAME - EventBridge bus (default: "default")
    - AWS_REGION - AWS region (optional, uses boto3 defaults)
  - **Dependencies**
    - Optional boto3 dependency: `pip install sqlery[eventbridge]`
    - Added to pyproject.toml extras
  - **Examples & Documentation**
    - Complete Lambda deployment example (serverless.yml)
    - Comprehensive README with architecture diagrams
    - Production checklist and troubleshooting guide
    - Cost optimization recommendations
- Benefits: Zero always-running processes, auto-scaling, pay-per-execution pricing

### v0.8.0
- Production Hardening
  - **Queue-Level Concurrency Control**
    - `allow_parallel` field (default: False) for per-queue concurrency
    - Checks queue_name instead of task_path
    - Email queues can run 100s of jobs in parallel
    - Migration queues run one at a time
  - **Job Timeout with External Kill**
    - `timeout_seconds` field with SIGALRM handler
    - `worker_pid` field stores process ID
    - External process kill (SIGTERM → wait 5s → SIGKILL)
    - Three-layer timeout enforcement
  - **Memory Leak Prevention**
    - One job per worker subprocess architecture
    - Auto-spawns next worker if more jobs exist
    - Complete resource cleanup per job
  - **Crash Recovery**
    - Stale job detection (running longer than 2x timeout)
    - External worker kill by stored PID
    - Automatic retry on crash
  - **Automatic Schedule Recomputation**
    - `next_run_at` recalculated when cron_expression changes
    - Smart handling of enabled/disabled transitions
    - No manual intervention needed
  - **Signal Handling & Process Management**
    - Complete signal handling for graceful shutdown and forced termination
    - **SIGALRM** - Job timeout enforcement
      - Worker subprocess sets SIGALRM before job execution
      - Raises TimeoutError when timeout exceeded
      - Job marked as failed with timeout error message
      - Used for `timeout_seconds` field enforcement
    - **SIGTERM/SIGKILL** - External worker kill
      - Stale job detection (running > 2x timeout)
      - External process kill using stored `worker_pid`
      - SIGTERM sent first (graceful termination attempt)
      - 5-second wait period for cleanup
      - SIGKILL sent if process still alive (forced termination)
      - Job marked as failed with appropriate error
    - **SIGINT/SIGTERM** - Graceful worker shutdown
      - Worker processes handle shutdown signals gracefully
      - Currently running job allowed to complete
      - No new jobs claimed after signal received
      - Clean process exit after current job finishes
      - Status updates committed before shutdown
    - **Process Lifecycle**
      - Each worker subprocess handles exactly one job
      - Fresh process for each job (zero memory accumulation)
      - Complete resource cleanup on process exit
      - Auto-spawn next worker if queue has more jobs
      - Zombie prevention via start_new_session=True
    - **Crash Recovery**
      - Jobs marked as "running" with no active process detected as stale
      - Automatic cleanup of orphaned jobs on worker startup
      - Retry mechanism for crashed jobs (respects max_retries)
      - PID storage enables external monitoring/management
    - Implementation: executor.py (timeout handler), daemon_manager.py (external kill), core/daemon.py (signal handlers)
- Migrations: 0005_concurrency_and_timeout, 0006_worker_pid
- Comprehensive tests (100+ new assertions)
- Full documentation in README

### v0.7.0
- Subprocess Trigger Mode (Recommended for Production)
  - New `SubprocessTriggerMiddleware` for direct subprocess spawning
  - `TRIGGER_MODE='subprocess'` option in settings
  - Fire-and-forget subprocess execution (no HTTP layer)
  - No network dependencies or port conflicts
  - Works with WSGI and ASGI servers
  - Process isolation with proper environment inheritance
  - Zombie prevention with start_new_session=True
  - Comprehensive tests (11 test cases)
  - Documented in README with comparison table
- Eliminates all HTTP trigger mode failure scenarios
- Recommended as default trigger mode for production deployments

### v0.6.2
- Robust Subprocess Resolution
  - Created `get_manage_py_path()` helper that uses `settings.BASE_DIR`
  - All subprocess commands now use absolute paths to manage.py
  - Works regardless of current working directory (CWD)
  - Fixes Docker, systemd, cloud function deployments
  - Checks both BASE_DIR and parent directory for manage.py
  - Clear error messages if BASE_DIR not configured or manage.py not found
  - Updated all subprocess calls in views.py and subprocess_executor.py
  - Comprehensive tests for path resolution and error handling
- Fixes critical production blocker for non-standard deployments

### v0.6.1
- Atomic Scheduler Claiming (SELECT FOR UPDATE SKIP LOCKED)
  - Implemented atomic task claiming in `run_due_tasks()`
  - Each scheduler atomically claims and processes different scheduled tasks
  - Prevents duplicate job enqueueing from concurrent schedulers
  - Job creation and `next_run_at` update within same transaction
  - Comprehensive concurrent scheduler tests
  - Safe for distributed deployments with multiple scheduler instances
- Fixes scheduler enqueueing race condition

### v0.6.0
- Atomic Job Claiming (SELECT FOR UPDATE SKIP LOCKED)
  - Implemented row-level locking with `select_for_update(skip_locked=True)`
  - Refactored `run_queue_workers()` to use atomic transactions
  - Updated `execute_job()` to handle already-running jobs
  - Prevents duplicate job execution across concurrent workers
  - Comprehensive concurrent execution tests
  - Documented Postgres requirement for SKIP LOCKED
  - Fallback behavior for other databases
- Fixes critical race condition in job claiming
- Safe for production with multiple workers

### v0.5.0
- Job Arguments (kwargs support)
  - kwargs JSONField on QueuedJob model
  - Tasks can accept keyword arguments
  - Executor passes kwargs to task functions
  - enqueue() and enqueue_at() accept **kwargs
  - @job decorator methods support **kwargs
  - Automatic serialization/deserialization
  - Kwargs preserved across retries
  - Supports all JSON-serializable types
- Migration 0004_job_arguments.py
- Comprehensive tests (40+ assertions in test_job_arguments.py)
- Full documentation with examples

### v0.4.0
- Automatic retry with exponential backoff
  - max_retries and retry_backoff fields on QueuedJob
  - Automatic retry job creation on failure
  - Exponential backoff delay calculation
  - Settings: DEFAULT_MAX_RETRIES, DEFAULT_RETRY_BACKOFF
- JobRun tracking (execution history)
  - runs JSONField on QueuedJob model
  - Records all execution attempts with full details
  - Admin interface displays run history in table format
  - History preserved across retry attempts
- Enhanced @job decorator
  - Support for max_retries and retry_backoff parameters
  - Retry configuration inherited from decorator
- Migration 0003_retry_logic.py
- Comprehensive tests (50+ new assertions)

### v0.3.0
- Subprocess execution mode (prevents memory leaks)
  - EXECUTION_MODE setting (auto/subprocess/django-tasks/thread)
  - Subprocess isolation for scheduler and workers
  - Automatic strategy selection
- HTTP trigger mode (ASGI-compatible)
  - HMAC-SHA256 signed internal requests
  - Async endpoint (`/_internal/worker`)
  - HttpTriggerMiddleware for post-response triggering
  - Zombie prevention via start_new_session
  - Works with uvicorn/ASGI for true async
- Comprehensive test suite
  - 200+ assertions across 10 test files
  - Complete coverage of all modules
  - Models, executor, API, middleware, triggers, admin, HTTP, subprocess
- Optional httpx dependency for HTTP mode

### v0.2.0
- QueuedJob model (queue system)
- Manual enqueue API (enqueue, enqueue_at)
- Queue routing and priority
- Updated executor for queue processing
- Dual-mode: scheduled + manual jobs
- run_jobs management command
- Enhanced Django admin
- Migration 0002_queue_system.py
- Comprehensive tests (100+ assertions)

### v0.1.0
- Initial MVP
- ScheduledTask model
- TaskExecution tracking
- Cron-based scheduling
- Middleware trigger
- Basic admin
- Zero dependencies (vendored crontabula)

---

## Migration Guide (v0.1 → v0.2)

### Breaking Changes
- `TaskExecution` renamed to `QueuedJob` (alias provided for compatibility)
- Added required fields to ScheduledTask: `queue_name`, `priority`
- Status added: "queued" (jobs start queued, not running)

### Migration Steps
1. Run migration: `python manage.py migrate sqlery`
2. Update settings if using custom config
3. Update any code referencing `TaskExecution` to use `QueuedJob`
4. Existing scheduled tasks will use default queue/priority

### Backward Compatibility
- `TaskExecution` alias maintained
- Old admin still works
- Middleware behavior unchanged (still auto-runs)

---

## Contributing

See implementation plan in `mvp.plan.md` for detailed technical specs.

For questions or contributions, open an issue on GitHub.
