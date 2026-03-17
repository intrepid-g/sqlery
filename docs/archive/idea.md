# Sqlery - Core Idea

## Vision Statement

A **production-ready Django package** that enables scheduled job execution **without requiring separate scheduler/worker processes**, using Postgres as the job store and leveraging django-tasks for execution.

---

## Primary Goal: No Separate Processes Required

**Main Feature**: Run scheduled jobs directly from Django without:
- ❌ Always-running scheduler processes (like Celery beat)
- ❌ Always-running worker pools (like K8s pods, RQ workers)
- ❌ External services (Redis, SQS, RabbitMQ)

**How**: Event-driven execution using django-tasks + Postgres storage.

---

## MVP Scope

### Core Features (MVP)

1. **Cron-based Task Scheduling**
   - Define tasks with cron strings (`"0 2 * * *"`)
   - Store task definitions in Postgres
   - Reference callable Python functions as jobs

2. **Django Admin Interface**
   - Create/edit/delete scheduled tasks via admin
   - View task execution history
   - Enable/disable tasks
   - Manual trigger capability

3. **Event-Driven Execution**
   - No constantly-polling scheduler
   - Tasks triggered when needed (via django-tasks)
   - Automatically calculates next run time

4. **Task Execution via django-tasks**
   - Leverage `django-tasks` for actual job invocation
   - Benefits: pluggable backends, retry logic, async support

### Out of Scope (MVP)

- ~~Complex retry strategies~~ (use django-tasks defaults)
- ~~Task dependencies/workflows~~ (single tasks only)
- ~~Real-time monitoring dashboards~~ (just admin + basic history)
- ~~Rate limiting~~ (can add later)
- ~~Distributed locking~~ (single-instance for MVP)

---

## Architecture

### Job Storage: Postgres

**ScheduledTask Model**:
```python
class ScheduledTask(models.Model):
    name = models.CharField(max_length=255)
    cron_expression = models.CharField(max_length=100)  # "0 2 * * *"
    task_path = models.CharField(max_length=500)  # "myapp.tasks.send_email"
    enabled = models.BooleanField(default=True)

    # Execution tracking
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField()

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**TaskExecution Model** (history):
```python
class TaskExecution(models.Model):
    task = models.ForeignKey(ScheduledTask)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(choices=['success', 'failed', 'running'])
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)
```

### Execution: django-tasks

Use `django-tasks` to handle:
- Task invocation (sync/async)
- Retries (via backend configuration)
- Pluggable backends (database, immediate, future serverless)

### Scheduler: Event-Driven

**No Polling Loop**. Instead:

**Option 1: Django Request Middleware** (simplest MVP)
- On each request, check if any tasks are due
- If yes, trigger via django-tasks (async)
- Minimal overhead, no separate process

**Option 2: Database Trigger** (more advanced)
- Postgres trigger on `next_run_at`
- Notify Django via LISTEN/NOTIFY
- Requires pg connection

**Option 3: Periodic Check** (fallback)
- Single management command: `python manage.py check_scheduled_tasks`
- Run via cron (ironic, but simple)
- Just triggers tasks, doesn't execute them

**MVP: Start with Option 1** (middleware approach)

---

## User Experience (MVP)

### 1. Define a Task

```python
# myapp/tasks.py
def send_daily_report():
    """Send daily report email."""
    # ... task logic
    return "Report sent"
```

### 2. Schedule via Admin

1. Go to Django Admin → Scheduled Tasks
2. Click "Add Scheduled Task"
3. Fill in:
   - **Name**: "Daily Report"
   - **Cron Expression**: `0 8 * * *` (8 AM daily)
   - **Task Path**: `myapp.tasks.send_daily_report`
   - **Enabled**: ✓
4. Save

### 3. Execution Happens Automatically

- Next request after 8 AM → middleware checks → task triggered via django-tasks
- Task runs asynchronously (depending on django-tasks backend)
- Execution logged in `TaskExecution` table
- `next_run_at` updated automatically

### 4. View History

- Django Admin → Task Executions
- See all runs: status, duration, output, errors

---

## Serverless Support (Bonus Feature)

**Not the main feature**, but should work:

### AWS Lambda Example

```python
# Use django-tasks with custom backend
TASKS_BACKEND = "django_tasks.backends.lambda_invoke.LambdaBackend"

SCHEDULED_TASKS_TRIGGER = "lambda"  # Instead of middleware
```

When task is due:
1. Lambda function checks Postgres for due tasks
2. Invokes another Lambda (via django-tasks) for each task
3. Updates execution history

**MVP**: Document how to set this up, don't build custom code for it.

---

## Key Design Decisions

### 1. Why django-tasks?

- ✅ Already solves task invocation
- ✅ Pluggable backends (immediate, DB, custom)
- ✅ Retries built-in
- ✅ Well-maintained
- ✅ We focus on scheduling, they handle execution

### 2. Why Event-Driven vs Polling?

**Polling (traditional)**:
```python
while True:
    check_for_due_tasks()
    time.sleep(60)  # Always running
```

**Event-Driven (our approach)**:
```python
# Middleware on each request
if should_check_tasks():
    trigger_due_tasks()
```

Benefits:
- No separate process to manage
- Works in serverless (Lambda, Cloud Run)
- Scales with app traffic
- Zero overhead when idle

Trade-off:
- Relies on app traffic (acceptable for MVP)
- Can add fallback cron check if needed

### 3. Why Postgres Not Dedicated Queue?

- ✅ Already in every Django app
- ✅ Full SQL querying for history
- ✅ Django Admin integration
- ✅ ACID guarantees
- ✅ No extra infrastructure

---

## MVP Implementation Phases

### Phase 1: Core Models & Admin (Week 1)
- [ ] `ScheduledTask` model with cron support
- [ ] `TaskExecution` model for history
- [ ] Django Admin configuration
- [ ] Cron parsing (use `croniter` library)
- [ ] Calculate `next_run_at` logic

### Phase 2: Execution Engine (Week 2)
- [ ] Integration with django-tasks
- [ ] Task discovery (import callable from path)
- [ ] Execution wrapper (create `TaskExecution`, handle errors)
- [ ] Update `next_run_at` after execution

### Phase 3: Trigger Mechanism (Week 3)
- [ ] Middleware to check for due tasks
- [ ] Throttling (don't check on every request)
- [ ] Manual trigger from admin
- [ ] Management command fallback

### Phase 4: Polish & Testing (Week 4)
- [ ] Unit tests (models, cron parsing, execution)
- [ ] Integration tests (full flow)
- [ ] Documentation
- [ ] Example project
- [ ] Package setup (pyproject.toml, README)

---

## Success Metrics (MVP)

A developer should be able to:
1. `pip install sqlery`
2. Add to `INSTALLED_APPS`
3. Run migrations
4. Define a task function
5. Schedule it via admin with cron string
6. See it execute automatically
7. View execution history

**All without configuring Redis, Celery, separate workers, or external services.**

---

## Dependencies

**Required**:
- Django ≥4.2
- django-tasks (for execution)
- croniter (for cron parsing)
- Postgres (for `SELECT FOR UPDATE SKIP LOCKED` if needed later)

**Optional**:
- django-extensions (for development)

---

## Similar Projects Comparison

| Feature | django-tasks-scheduler | django-cron | This Project |
|---------|----------------------|-------------|--------------|
| **Cron syntax** | ✅ | ✅ | ✅ |
| **Admin UI** | ✅ | ❌ | ✅ |
| **No separate process** | ❌ (needs scheduler) | ❌ (needs management command loop) | ✅ |
| **Execution backend** | Custom | Custom | django-tasks (pluggable) |
| **Serverless-friendly** | ❌ | ❌ | ✅ |
| **History tracking** | ✅ | ❌ | ✅ |
| **Simple setup** | Medium | Medium | ✅ High |

---

## Design Decisions (Resolved)

1. **How to handle missed runs?**
   - If app is down during scheduled time, run on next check?
   - Or skip and wait for next cron interval?
   - **✅ DECISION**: Execute missed runs on next check
   - Track "last_run_at" to detect missed executions

2. **Concurrency control?**
   - What if same task triggered multiple times (e.g., slow execution + multiple requests)?
   - **✅ DECISION**: Use TaskExecution status check for MVP
   - Check for existing `status='running'` before executing
   - If needed later: Upgrade to Postgres advisory locks

   **MVP Implementation**:
   ```python
   if TaskExecution.objects.filter(task=task, status='running').exists():
       return  # Already running, skip
   ```

3. **Timezone handling?**
   - **✅ DECISION**: All times stored as UTC
   - Cron expressions evaluated in UTC
   - Document clearly in admin and README

4. **Task arguments/parameters?**
   - **✅ DECISION**: No task arguments for MVP
   - Tasks must be zero-argument callables
   - Keeps implementation lean and simple
   - Future: Can add JSONField for kwargs if needed

5. **Serverless vs Traditional?**
   - **✅ REQUIREMENT**: Must work in BOTH environments
   - Traditional: Middleware-based trigger
   - Serverless: Management command invoked by external scheduler (cron/EventBridge)
   - Same execution engine for both

---

## Repository Structure

```
sqlery/
├── src/
│   └── sqlery/
│       ├── models.py          # ScheduledTask, TaskExecution
│       ├── admin.py           # Admin configuration
│       ├── middleware.py      # Event-driven trigger
│       ├── executor.py        # Task execution logic
│       ├── cron.py            # Cron parsing utils
│       └── management/
│           └── commands/
│               └── check_scheduled_tasks.py
├── tests/
├── docs/
├── examples/
│   └── demo_project/
├── pyproject.toml
└── README.md
```

---

## Next Steps

1. ✅ Document the idea (this file)
2. **Analyze inspiration-example & helper-invoker**
   - What patterns to adopt?
   - What to avoid?
   - API design inspiration
3. **Design the API**
   - Models
   - Admin interface
   - Settings configuration
4. **Build Phase 1: Models + Admin**
5. **Iterate based on feedback**

---

## Philosophy

**Keep it simple.**
- Don't reinvent what django-tasks already does
- Don't add features "just in case"
- Make the 80% use case trivial
- Document the 20% edge cases clearly

**Make it obvious.**
- Clear error messages
- Obvious admin interface
- Predictable behavior
- No magic

**Make it reliable.**
- Production-ready from day 1
- Proper error handling
- Comprehensive tests
- Safe defaults
