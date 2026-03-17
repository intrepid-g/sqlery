# Package Split Migration Guide

## Overview

Starting with version 0.8.0, sqlery is preparing to split into three separate packages:

1. **`sqlery`** (core) - Framework-agnostic job queue and scheduling
2. **`django-sqlery`** - Django ORM integration
3. **`fastapi-sqlery`** - FastAPI web UI and REST API

This guide explains the changes and how to migrate your code.

---

## 🎯 Current Status (v0.8.0)

**Monorepo with Organized Structure** ✅

All code still lives in the `sqlery` package, but has been reorganized:

```
sqlery/
├── backends/          # Core: Database backends (PostgreSQL, SQLite)
├── core/              # Core: Job queue logic
├── django_sqlery/     # Django: ORM models, admin, management commands
├── fastapi_sqlery/    # FastAPI: Web UI, REST API, CLI
├── schema.py          # Core: SQL schema
├── utils.py           # Core: Utilities
└── rate_limit_utils.py  # Core: Rate limiting
```

**What Changed:**
- Django code moved to `src/sqlery/django_sqlery/` subfolder
- FastAPI code moved to `src/sqlery/fastapi_sqlery/` subfolder
- Original files converted to commented stubs (for backward compatibility)
- CLI entry points updated to use new paths

**Breaking Changes:** NONE (yet)

All existing import paths still work via compatibility stubs.

---

## 📦 Future Architecture (v1.0.0+)

Eventually, these will become separate PyPI packages:

### Core Package: `sqlery`

**Purpose:** Framework-agnostic job queue and cron scheduling

**Installation:**
```bash
pip install sqlery
```

**What's Included:**
- Database backends (PostgreSQL, SQLite)
- Core job queue logic
- Worker implementation
- Scheduling system
- Rate limiting
- NO Django or FastAPI code

**Dependencies:** Minimal (croniter, psycopg2-binary, uuid6)

**Use When:**
- You want a standalone job queue
- You're using Flask, vanilla Python, or another framework
- You want minimal dependencies

### Django Package: `django-sqlery`

**Purpose:** Django ORM integration for sqlery

**Installation:**
```bash
pip install django-sqlery  # Automatically installs sqlery core
```

**What's Included:**
- Django ORM models (QueuedJob, ScheduledTask, Worker)
- Django Admin integration
- Management commands (workers, daemon, cleanup_jobs)
- Django-specific decorators
- Templates and dashboard views

**Dependencies:** django>=4.2, sqlery (core)

**Use When:**
- You're using Django
- You want ORM-based job storage
- You want Django Admin integration

### FastAPI Package: `fastapi-sqlery`

**Purpose:** FastAPI web UI and REST API for sqlery

**Installation:**
```bash
pip install fastapi-sqlery  # Automatically installs sqlery core
```

**What's Included:**
- FastAPI web application
- REST API for job management
- Web dashboard UI
- CLI commands (sqlery, sqlery-worker, sqlery-web)

**Dependencies:** fastapi>=0.104.0, uvicorn, sqlery (core)

**Use When:**
- You want a web UI for managing jobs
- You need a REST API for job queue
- You're running standalone (no Django)

---

## 🔄 Migration Path

### Phase 1: Current State (v0.8.0) ✅

**Status:** Monorepo with organized structure

**Import Paths:**

```python
# Django imports (current)
from sqlery.django_sqlery.models import QueuedJob
from sqlery.django_sqlery.decorators import job
from sqlery.django_sqlery.queue import enqueue

# FastAPI imports (current)
from sqlery.fastapi_sqlery import create_app
from sqlery.fastapi_sqlery.backend import FastAPIBackend
```

**Backward Compatibility:**

Old imports still work via stubs:

```python
# These still work (via stubs)
from sqlery.models import QueuedJob  # ⚠️ Deprecated, but works
from sqlery.decorators import job     # ⚠️ Deprecated, but works
```

**Action Required:** NONE

Everything still works. However, we **recommend** updating to new import paths now to prepare for v1.0.

### Phase 2: Deprecation Warnings (v0.9.0)

**Status:** Coming soon

**Changes:**
- Old import paths will show `DeprecationWarning`
- Stubs will log warnings when used
- Documentation updated to show only new paths

**Action Required:** Update imports to new paths

```python
# OLD (will show warnings)
from sqlery.models import QueuedJob

# NEW (recommended)
from sqlery.django_sqlery.models import QueuedJob
```

### Phase 3: Separate Packages (v1.0.0+)

**Status:** Future

**Changes:**
- Packages published separately to PyPI
- `sqlery` (core) contains only framework-agnostic code
- `django-sqlery` and `fastapi-sqlery` are separate packages
- Old stubs removed (breaking change)

**Action Required:** Update `requirements.txt` or `pyproject.toml`

```toml
# OLD (v0.x)
[dependencies]
sqlery = "^0.8.0"

# NEW (v1.x) - Django users
[dependencies]
django-sqlery = "^1.0.0"  # Installs sqlery core automatically

# NEW (v1.x) - FastAPI users
[dependencies]
fastapi-sqlery = "^1.0.0"  # Installs sqlery core automatically

# NEW (v1.x) - Standalone users
[dependencies]
sqlery = "^1.0.0"  # Core only, no Django/FastAPI
```

**Import Paths (v1.0+):**

```python
# Django users - just drop "sqlery." prefix
from django_sqlery.models import QueuedJob
from django_sqlery.decorators import job

# FastAPI users - just drop "sqlery." prefix
from fastapi_sqlery import create_app
from fastapi_sqlery.backend import FastAPIBackend

# Standalone users - framework-agnostic imports
from sqlery.backends import create_backend
from sqlery.core.worker import Worker
```

---

## 🛠️ How to Migrate Your Code

### For Django Users

#### Step 1: Update Imports (Recommended Now)

**Before (v0.7 and earlier):**
```python
from sqlery.models import QueuedJob, ScheduledTask
from sqlery.decorators import job
from sqlery.queue import enqueue
from sqlery.executor import TaskExecutor
```

**After (v0.8+):**
```python
from sqlery.django_sqlery.models import QueuedJob, ScheduledTask
from sqlery.django_sqlery.decorators import job
from sqlery.django_sqlery.queue import enqueue
from sqlery.django_sqlery.executor import TaskExecutor
```

**After (v1.0+ - future):**
```python
# Just drop "sqlery." prefix
from django_sqlery.models import QueuedJob, ScheduledTask
from django_sqlery.decorators import job
from django_sqlery.queue import enqueue
from django_sqlery.executor import TaskExecutor
```

#### Step 2: Update Django Settings

**No changes required** - settings remain the same:

```python
# settings.py - No changes needed
INSTALLED_APPS = [
    # ...
    'sqlery',  # Still works in v0.8+
    # Will become 'django_sqlery' in v1.0+
]

DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    # ... all settings unchanged
}
```

#### Step 3: Update Management Commands

**No changes required** - commands remain the same:

```bash
# All commands still work
python manage.py daemon start
python manage.py workers list
python manage.py cleanup_jobs auto
```

### For FastAPI / Standalone Users

#### Step 1: Update Imports (Recommended Now)

**Before (v0.7 and earlier):**
```python
from sqlery.fastapi.app import create_app
from sqlery.fastapi.backend import FastAPIBackend
```

**After (v0.8+):**
```python
from sqlery.fastapi_sqlery.app import create_app
from sqlery.fastapi_sqlery.backend import FastAPIBackend
```

**After (v1.0+ - future):**
```python
# Just drop "sqlery." prefix
from fastapi_sqlery.app import create_app
from fastapi_sqlery.backend import FastAPIBackend
```

#### Step 2: Update CLI Commands

**Before (v0.7):**
```bash
# Old entry points (if they existed)
sqlery-start
```

**After (v0.8+):**
```bash
# New entry points (same commands)
sqlery worker --queues default,email
sqlery web --port 8000
sqlery migrate
```

**No changes required** - CLI commands are the same.

### For Core / Standalone Users (No Framework)

**Good news:** Core functionality stays in `sqlery` package!

```python
# These imports won't change
from sqlery.backends import create_backend
from sqlery.core.worker import Worker
from sqlery.core.job import Job
from sqlery.utils import serialize_job_arguments
```

---

## 🔍 How to Find Code That Needs Updating

### Search for Old Import Patterns

```bash
# Find all files importing from old paths
grep -r "from sqlery\.models import" . --include="*.py"
grep -r "from sqlery\.decorators import" . --include="*.py"
grep -r "from sqlery\.queue import" . --include="*.py"
grep -r "from sqlery\.fastapi\." . --include="*.py"
```

### Use Your IDE

Most IDEs (PyCharm, VSCode) will show deprecation warnings when you use old imports.

---

## 📊 Quick Reference: Import Path Changes

### Django Imports

| Old Path (v0.7) | Current Path (v0.8) | Future Path (v1.0+) |
|----------------|---------------------|---------------------|
| `sqlery.models` | `sqlery.django_sqlery.models` | `django_sqlery.models` |
| `sqlery.admin` | `sqlery.django_sqlery.admin` | `django_sqlery.admin` |
| `sqlery.decorators` | `sqlery.django_sqlery.decorators` | `django_sqlery.decorators` |
| `sqlery.queue` | `sqlery.django_sqlery.queue` | `django_sqlery.queue` |
| `sqlery.executor` | `sqlery.django_sqlery.executor` | `django_sqlery.executor` |
| `sqlery.settings` | `sqlery.django_sqlery.settings` | `django_sqlery.settings` |

### FastAPI Imports

| Old Path (v0.7) | Current Path (v0.8) | Future Path (v1.0+) |
|----------------|---------------------|---------------------|
| `sqlery.fastapi.app` | `sqlery.fastapi_sqlery.app` | `fastapi_sqlery.app` |
| `sqlery.fastapi.backend` | `sqlery.fastapi_sqlery.backend` | `fastapi_sqlery.backend` |
| `sqlery.fastapi.cli` | `sqlery.fastapi_sqlery.cli` | `fastapi_sqlery.cli` |

### Core Imports (No Change)

| Path (all versions) |
|-------------------|
| `sqlery.backends` |
| `sqlery.core` |
| `sqlery.utils` |
| `sqlery.schema` |

---

## ❓ FAQ

### Q: Do I need to migrate now?

**A:** No, but it's recommended. Old imports still work via stubs in v0.8.0.

### Q: When will old imports stop working?

**A:** In v1.0.0 (date TBD). You'll have plenty of warning via deprecation messages in v0.9.0.

### Q: Will my Django project break?

**A:** No. We're maintaining full backward compatibility until v1.0.

### Q: Can I mix old and new imports?

**A:** Yes, but not recommended. Pick one style and stick with it.

### Q: What if I use both Django and FastAPI?

**A:** Install both packages in v1.0:
```bash
pip install django-sqlery fastapi-sqlery
```

### Q: Why split the packages?

**A:** Benefits:
- **Smaller dependencies** - Django users don't need FastAPI installed
- **Clearer separation** - Framework-specific code is isolated
- **Independent versioning** - Django and FastAPI integrations can evolve separately
- **Easier maintenance** - Each package has focused purpose

### Q: Will there be breaking changes?

**A:** Only in import paths. The API (methods, arguments, behavior) remains the same.

---

## 🐛 Troubleshooting

### Import Error After Update

**Error:**
```
ImportError: cannot import name 'Queue' from 'sqlery.queue'
```

**Cause:** Package split left some stubs that may not work correctly.

**Fix:** Use new import paths:
```python
# Instead of:
from sqlery.queue import Queue

# Use:
from sqlery.django_sqlery.queue import Queue  # Django
# OR
from sqlery.core.queue import Queue  # Core (if it exists)
```

### Deprecation Warnings

**Warning:**
```
DeprecationWarning: Importing from sqlery.models is deprecated.
Use sqlery.django_sqlery.models instead.
```

**Fix:** Update your imports as shown in the warning.

### Django Admin Not Loading

**Error:**
```
django.core.exceptions.ImproperlyConfigured:
Application labels aren't unique, duplicates: sqlery
```

**Cause:** Both old and new app configs registered.

**Fix:** Use only one in `INSTALLED_APPS`:
```python
INSTALLED_APPS = [
    # OLD (v0.7)
    # 'sqlery',

    # NEW (v0.8+)
    'sqlery.django_sqlery',  # Use this
]
```

---

## 📚 Additional Resources

- [PACKAGE_SPLIT_PLAN.md](../PACKAGE_SPLIT_PLAN.md) - Detailed technical plan
- [DJANGO_MIGRATION_SUMMARY.md](../DJANGO_MIGRATION_SUMMARY.md) - Django migration details
- [FASTAPI_MIGRATION_SUMMARY.md](../FASTAPI_MIGRATION_SUMMARY.md) - FastAPI migration details
- [CHAOS_TEST_FINDINGS.md](../CHAOS_TEST_FINDINGS.md) - Issues found during migration

---

## 🤝 Need Help?

- **GitHub Issues:** [https://github.com/intrepid-g/sqlery/issues](https://github.com/intrepid-g/sqlery/issues)
- **Discussions:** [https://github.com/intrepid-g/sqlery/discussions](https://github.com/intrepid-g/sqlery/discussions)

---

**Last Updated:** 2025-11-13
**Version:** 0.8.0
