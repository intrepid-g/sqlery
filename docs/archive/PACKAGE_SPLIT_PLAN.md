# Package Split Plan: sqlery → sqlery + django-sqlery

**Date**: 2025-11-12
**Goal**: Split codebase into two packages following the pattern of `rq` / `django-rq`

---

## 📦 Package Architecture

### Current State
- Single package: `sqlery` (v0.8.0)
- Supports both Django and Standalone modes
- Django dependencies are optional extras
- 72 Python files (excluding migrations)

### Target State

**Package 1: `sqlery` (Core)**
- Pure Python job queue + cron scheduling
- No Django dependencies
- Works standalone with FastAPI, Flask, or any Python app
- Similar to: `rq`, `celery`, `arq`

**Package 2: `django-sqlery` (Django Integration)**
- Django app that wraps `sqlery`
- Depends on `sqlery` as a requirement
- Provides Django ORM models, admin, management commands
- Similar to: `django-rq`, `django-celery-beat`

---

## 🗂️ File Classification

### Core Package (`sqlery`)

**Backends** (Standalone-focused)
```
src/sqlery/backends/
├── __init__.py
├── base.py              ← Abstract interfaces
├── sync_backend.py      ← Sync database backend
├── async_backend.py     ← Async database backend
└── factory.py           ← Backend factory
```

**Core Logic** (Framework-agnostic)
```
src/sqlery/core/
├── __init__.py
├── job.py               ← @job decorator (standalone)
├── queue.py             ← Standalone enqueue
├── worker.py            ← Standalone worker
├── models.py            ← SQLModel models (optional)
└── cli.py               ← CLI commands
```

**FastAPI Integration** (Standalone)
```
src/sqlery/fastapi/
├── __init__.py
├── cli.py               ← FastAPI CLI
├── routes.py            ← Web UI routes
├── dashboard.py         ← Dashboard views
└── templates/           ← Jinja2 templates
```

**Utilities** (Framework-agnostic)
```
src/sqlery/
├── schema.py            ← SQL schema definitions
├── utils.py             ← Utility functions
├── rate_limit_utils.py  ← Rate limiting
└── cleanup.py           ← Cleanup utilities
```

**Status**: Keep in core `sqlery` package

---

### Django Package (`django-sqlery`)

**Django ORM Models**
```
django_sqlery/
├── models.py            ← QueuedJob, ScheduledTask, Worker (Django ORM)
├── admin.py             ← Django admin interface
├── apps.py              ← Django app config
├── urls.py              ← Django URL patterns
└── views.py             ← Django views
```

**Django Management Commands**
```
django_sqlery/management/commands/
├── run_jobs.py          ← Django command: python manage.py run_jobs
├── run_scheduled_tasks.py
├── cleanup_jobs.py
├── workers.py
└── daemon.py
```

**Django Migrations**
```
django_sqlery/migrations/
├── 0001_initial.py
├── 0002_worker_multi_worker.py
├── ...
└── 0011_add_version_field.py
```

**Django Integration**
```
django_sqlery/
├── decorators.py        ← @job decorator (Django-specific)
├── queue.py             ← enqueue() using Django ORM
├── executor.py          ← TaskExecutor using Django ORM
├── middleware.py        ← Django middleware
├── settings.py          ← Django settings
└── db_compat.py         ← Django database compatibility
```

**Django Worker Process**
```
django_sqlery/
├── worker_process.py    ← Django worker
├── worker_registry.py   ← Worker registration
├── worker_claiming.py   ← Worker job claiming
└── daemon_worker.py     ← Daemon worker
```

**Django Templates**
```
django_sqlery/templates/
└── admin/
    └── sqlery/
        └── scheduledtask/
            └── change_form.html
```

**Django Dashboard**
```
django_sqlery/
├── dashboard_views.py   ← Django dashboard
└── registries.py        ← Django-specific registries
```

**Status**: Move to `django-sqlery` package

---

## 🔄 Migration Strategy

### Phase 1: Prepare Core Package
1. **Create clear separation in existing codebase**
   - Move Django-agnostic code to `src/sqlery/core/`
   - Ensure `core/` has zero Django imports
   - Abstract away Django-specific features

2. **Dual-mode support (temporary)**
   - Keep both modes working in same package
   - Use conditional imports: `try: import django except: pass`
   - Deprecation warnings for Django usage of core package

### Phase 2: Extract Django Package
1. **Create new repo: `django-sqlery`**
   - New `pyproject.toml` with `sqlery` as dependency
   - Copy Django-specific files
   - Update imports: `from sqlery import ...` → `from sqlery.core import ...`

2. **Django package structure**
   ```
   django-sqlery/
   ├── pyproject.toml
   ├── README.md
   ├── src/
   │   └── django_sqlery/
   │       ├── __init__.py
   │       ├── models.py
   │       ├── admin.py
   │       ├── apps.py
   │       ├── management/
   │       ├── migrations/
   │       └── templates/
   └── tests/
   ```

### Phase 3: Update Core Package
1. **Remove Django dependencies**
   - Remove `django` from optional-dependencies
   - Remove Django-specific files
   - Update README to point to `django-sqlery`

2. **Version bump**
   - `sqlery` → v1.0.0 (standalone-only)
   - `django-sqlery` → v1.0.0 (first release)

---

## 📋 Detailed File Mapping

### Files to KEEP in `sqlery` (Core)

**Already in good shape:**
- ✅ `src/sqlery/backends/` (all files) - No Django deps
- ✅ `src/sqlery/core/` (all files) - Framework-agnostic
- ✅ `src/sqlery/fastapi/` (all files) - FastAPI mode
- ✅ `src/sqlery/schema.py` - Pure SQL
- ✅ `src/sqlery/utils.py` - Utilities
- ✅ `src/sqlery/rate_limit_utils.py` - Rate limiting
- ✅ `src/sqlery/cleanup.py` - Cleanup logic

**Need Django removal/abstraction:**
- ⚠️ `src/sqlery/registries.py` - Uses Django cache
- ⚠️ `src/sqlery/middleware.py` - Django middleware
- ⚠️ `src/sqlery/subprocess_middleware.py` - Django specific
- ⚠️ `src/sqlery/http_trigger_middleware.py` - Django middleware

### Files to MOVE to `django-sqlery`

**Django ORM & Admin:**
- 📦 `src/sqlery/models.py` → `django_sqlery/models.py`
- 📦 `src/sqlery/admin.py` → `django_sqlery/admin.py`
- 📦 `src/sqlery/apps.py` → `django_sqlery/apps.py`
- 📦 `src/sqlery/urls.py` → `django_sqlery/urls.py`
- 📦 `src/sqlery/views.py` → `django_sqlery/views.py`

**Django Management:**
- 📦 `src/sqlery/management/` → `django_sqlery/management/`
- 📦 `src/sqlery/migrations/` → `django_sqlery/migrations/`

**Django Integration:**
- 📦 `src/sqlery/decorators.py` → `django_sqlery/decorators.py`
- 📦 `src/sqlery/queue.py` → `django_sqlery/queue.py`
- 📦 `src/sqlery/executor.py` → `django_sqlery/executor.py`
- 📦 `src/sqlery/middleware.py` → `django_sqlery/middleware.py`
- 📦 `src/sqlery/settings.py` → `django_sqlery/settings.py`
- 📦 `src/sqlery/db_compat.py` → `django_sqlery/db_compat.py`

**Django Worker:**
- 📦 `src/sqlery/worker_process.py` → `django_sqlery/worker_process.py`
- 📦 `src/sqlery/worker_registry.py` → `django_sqlery/worker_registry.py`
- 📦 `src/sqlery/worker_claiming.py` → `django_sqlery/worker_claiming.py`
- 📦 `src/sqlery/daemon_worker.py` → `django_sqlery/daemon_worker.py`
- 📦 `src/sqlery/daemon_manager.py` → `django_sqlery/daemon_manager.py`
- 📦 `src/sqlery/daemon_middleware.py` → `django_sqlery/daemon_middleware.py`
- 📦 `src/sqlery/subprocess_executor.py` → `django_sqlery/subprocess_executor.py`
- 📦 `src/sqlery/subprocess_middleware.py` → `django_sqlery/subprocess_middleware.py`

**Django Dashboard:**
- 📦 `src/sqlery/dashboard_views.py` → `django_sqlery/dashboard_views.py`
- 📦 `src/sqlery/registries.py` → `django_sqlery/registries.py`
- 📦 `src/sqlery/http_trigger_middleware.py` → `django_sqlery/http_trigger_middleware.py`

**Django Templates:**
- 📦 `src/sqlery/templates/admin/` → `django_sqlery/templates/admin/`

**Special Case - Dual Mode:**
- 📦 `src/sqlery/django/` - Already separated, move to `django_sqlery/`

---

## 📊 Import Analysis

### Current Django Imports (36 files)

Files that import Django need to be:
1. Moved to `django-sqlery` OR
2. Refactored to remove Django dependency OR
3. Made conditional (`try: import django`)

**High Django Coupling:**
- `models.py` - 100% Django (QueuedJob, ScheduledTask)
- `admin.py` - 100% Django
- `executor.py` - Heavy Django usage (timezone, ORM)
- `decorators.py` - Uses Django models
- `management/commands/` - All Django commands

**Medium Django Coupling:**
- `middleware.py` - Django cache, settings
- `registries.py` - Django cache
- `worker_*.py` - Django ORM queries

**Low Django Coupling:**
- `utils.py` - Mostly pure, some Django imports
- `cleanup.py` - Django queries but could be abstracted

---

## 🎯 API Design: Core Package

### Core `sqlery` Public API

**Job Decorator:**
```python
from sqlery import job

@job(queue="email", priority=10)
def send_email(to, subject, body):
    # Send email
    pass

# Enqueue job
send_email.enqueue(to="user@example.com", subject="Hello", body="World")
```

**Direct Enqueue:**
```python
from sqlery import enqueue, enqueue_at
from datetime import datetime, timedelta

# Immediate
job = enqueue("myapp.tasks.send_email", to="user@example.com")

# Scheduled
run_at = datetime.now() + timedelta(hours=1)
job = enqueue_at("myapp.tasks.send_email", run_at, to="user@example.com")
```

**Worker:**
```python
from sqlery import Worker

worker = Worker(queues=["default", "email"])
worker.start()
```

**Backend:**
```python
from sqlery.backends import create_backend, set_default_backend

# PostgreSQL
backend = create_backend("postgresql://localhost/mydb")
set_default_backend(backend)

# SQLite
backend = create_backend("sqlite:///jobs.db")
```

**Scheduled Tasks:**
```python
from sqlery import schedule

# Cron-based scheduling
schedule.create(
    name="daily_cleanup",
    task_path="myapp.tasks.cleanup",
    cron_expression="0 2 * * *",  # 2 AM daily
    enabled=True
)
```

**CLI:**
```bash
# Worker
sqlery worker --queues default,email --concurrency 4

# Web UI
sqlery web --port 8000

# Migrations
sqlery migrate

# Scheduled tasks
sqlery scheduler
```

---

## 🎯 API Design: Django Package

### `django-sqlery` Public API

**Installation:**
```bash
pip install django-sqlery  # Automatically installs sqlery
```

**Settings:**
```python
# settings.py
INSTALLED_APPS = [
    ...
    'django_sqlery',
]

DATABASES = {
    'default': {...}
}

# Optional: Configure sqlery
SQLERY = {
    'BACKEND': 'django',  # Use Django ORM backend
    'QUEUE_SETTINGS': {
        'default': {'concurrency': 4},
        'email': {'concurrency': 2},
    },
}
```

**Job Decorator (Django-aware):**
```python
from django_sqlery import job

@job(queue="email")
def send_email(to, subject, body):
    # Can use Django ORM, timezone, etc.
    from django.utils import timezone
    from myapp.models import EmailLog

    EmailLog.objects.create(to=to, sent_at=timezone.now())
```

**Management Commands:**
```bash
# Worker
python manage.py run_jobs --queues default,email --concurrency 4

# Scheduler
python manage.py run_scheduled_tasks

# Cleanup
python manage.py cleanup_jobs --days 30

# Workers management
python manage.py workers list
python manage.py workers stop <worker_id>
```

**Django ORM:**
```python
from django_sqlery.models import QueuedJob, ScheduledTask, Worker

# Query jobs
jobs = QueuedJob.objects.filter(status='queued')

# Create scheduled task
task = ScheduledTask.objects.create(
    name="daily_backup",
    task_path="myapp.tasks.backup",
    cron_expression="0 3 * * *",
    enabled=True
)
```

**Admin:**
- Django admin interface for jobs, tasks, workers
- Custom actions (requeue, cancel, retry)
- Filtering, searching

---

## 📦 Package Configuration

### `sqlery/pyproject.toml` (Core)

```toml
[project]
name = "sqlery"
version = "1.0.0"
description = "Standalone job queue + cron scheduling for Python"
dependencies = [
    "croniter>=2.0.0",
    "databases>=0.9.0",
    "uuid6>=2024.1.0",
]

[project.optional-dependencies]
# PostgreSQL support
postgresql = ["asyncpg>=0.30.0", "psycopg2-binary>=2.9.0"]

# SQLite support
sqlite = ["aiosqlite>=0.21.0"]

# FastAPI mode
fastapi = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
]

# CLI
cli = [
    "typer>=0.9.0",
    "rich>=13.0.0",
]

# All features
all = [
    "asyncpg>=0.30.0",
    "psycopg2-binary>=2.9.0",
    "aiosqlite>=0.21.0",
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
    "typer>=0.9.0",
    "rich>=13.0.0",
]

[project.scripts]
sqlery = "sqlery.cli:app"
```

### `django-sqlery/pyproject.toml` (Django)

```toml
[project]
name = "django-sqlery"
version = "1.0.0"
description = "Django integration for sqlery job queue"
dependencies = [
    "sqlery>=1.0.0",
    "django>=4.2",
]

[project.optional-dependencies]
# Additional features
http = ["httpx>=0.24.0"]
eventbridge = ["boto3>=1.34.0"]

# All features
all = [
    "httpx>=0.24.0",
    "boto3>=1.34.0",
]
```

---

## 🔧 Implementation Steps

### Step 1: Analyze & Document ✅
- [x] Analyze current file structure
- [x] Identify Django dependencies
- [x] Create migration plan
- [ ] Review with stakeholders

### Step 2: Refactor Core (In Current Repo)
- [ ] Create `src/sqlery/core/` structure
- [ ] Move framework-agnostic code to `core/`
- [ ] Abstract Django-specific features
- [ ] Add conditional imports for backward compat
- [ ] Update tests to work in both modes
- [ ] Document deprecated Django usage in core

### Step 3: Create Django Package (New Repo)
- [ ] Create `django-sqlery` repository
- [ ] Set up package structure
- [ ] Copy Django-specific files
- [ ] Update imports to use `sqlery` core
- [ ] Create Django-specific tests
- [ ] Write Django integration docs

### Step 4: Update Core Package
- [ ] Remove Django-specific files
- [ ] Update README (point to django-sqlery)
- [ ] Version bump to 1.0.0
- [ ] Publish to PyPI

### Step 5: Publish Django Package
- [ ] Version 1.0.0
- [ ] Publish to PyPI
- [ ] Update docs

### Step 6: Migration Guide
- [ ] Write upgrade guide for existing users
- [ ] Provide examples for both packages
- [ ] Create compatibility shims if needed

---

## 🚨 Breaking Changes

### For Existing Django Users

**Before (v0.8.0):**
```python
# Install
pip install sqlery[django]

# Import
from sqlery import job
from sqlery.models import QueuedJob
```

**After (v1.0.0):**
```python
# Install
pip install django-sqlery  # Automatically installs sqlery

# Import
from django_sqlery import job
from django_sqlery.models import QueuedJob
```

### For Existing Standalone Users

**Before (v0.8.0):**
```python
# Install
pip install sqlery[standalone]

# Import (messy, mixed with Django)
from sqlery.core.job import job
from sqlery.backends import create_backend
```

**After (v1.0.0):**
```python
# Install
pip install sqlery  # Pure, no Django bloat

# Import (clean)
from sqlery import job
from sqlery.backends import create_backend
```

---

## 📚 Similar Projects (Reference)

### RQ + Django-RQ
- `rq` - Pure Python job queue (Redis-based)
- `django-rq` - Django integration for rq
- Pattern: Separate packages, django-rq depends on rq

### Celery + Django-Celery-Beat
- `celery` - Distributed task queue
- `django-celery-beat` - Django periodic tasks
- Pattern: Core separate from Django integration

### APScheduler + Django-APScheduler
- `apscheduler` - Python scheduling library
- `django-apscheduler` - Django integration
- Pattern: Separate packages

---

## ✅ Benefits of Split

### For Core Users (Standalone)
- ✅ No Django bloat (smaller install)
- ✅ Cleaner imports
- ✅ Faster installation
- ✅ Works with any framework (Flask, FastAPI, etc.)
- ✅ Better for microservices

### For Django Users
- ✅ Clear Django integration
- ✅ Explicit dependency on core
- ✅ Better versioning (can update Django integration separately)
- ✅ Cleaner package naming
- ✅ Follows Django ecosystem conventions

### For Maintainers
- ✅ Cleaner separation of concerns
- ✅ Easier to test (two separate test suites)
- ✅ Independent versioning
- ✅ Easier to maintain (clear boundaries)
- ✅ Better CI/CD (can test/deploy separately)

---

## ⚠️ Risks & Mitigation

### Risk 1: Breaking Existing Users
**Mitigation:**
- Provide clear migration guide
- Keep v0.8.x maintained for 6 months
- Add deprecation warnings in v0.9.0
- Provide compatibility shims

### Risk 2: Duplicate Code
**Mitigation:**
- Core package provides all logic
- Django package is thin wrapper
- Share common utilities via core

### Risk 3: Version Sync Issues
**Mitigation:**
- Pin `django-sqlery` to specific `sqlery` versions
- Use semantic versioning strictly
- Document version compatibility matrix

### Risk 4: CI/CD Complexity
**Mitigation:**
- Separate CI for each package
- Integration tests in django-sqlery
- End-to-end tests in separate repo

---

## 📅 Timeline Estimate

- **Phase 1 (Analysis & Planning)**: 1 week ✅
- **Phase 2 (Core Refactor)**: 2-3 weeks
- **Phase 3 (Django Extract)**: 2 weeks
- **Phase 4 (Core Cleanup)**: 1 week
- **Phase 5 (Django Publish)**: 1 week
- **Phase 6 (Migration Guide)**: 1 week

**Total**: 8-10 weeks

---

## 🎯 Next Steps

1. **Review this plan** - Get feedback on approach
2. **Create issues** - Track each step as GitHub issues
3. **Start Phase 2** - Begin core refactoring
4. **Test extensively** - Ensure no regressions
5. **Document everything** - Write migration guide as we go

---

**Status**: DRAFT - Awaiting Review
<!-- **Author**: Claude Code -->
**Date**: 2025-11-12
