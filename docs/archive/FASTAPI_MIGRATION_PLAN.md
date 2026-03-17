# FastAPI Code Migration Plan

**Branch**: feature/package-split
**Goal**: Move FastAPI-specific code to `src/sqlery/fastapi_sqlery/` subfolder
**Strategy**: Comment out (not delete) FastAPI code, add #CLEANUP markers
**Pattern**: Same as django_sqlery migration

---

## Package Naming

**PyPI Package**: `fastapi-sqlery` (with hyphen)
**Python Import**: `fastapi_sqlery` (with underscore)

**Similar to**:
- `django-sqlery` → `django_sqlery`
- Core package: `sqlery` (no framework)

---

## Files to Move to `src/sqlery/fastapi_sqlery/`

### FastAPI Integration Files

**Source → Destination**

1. `src/sqlery/fastapi/__init__.py` → `src/sqlery/fastapi_sqlery/__init__.py`
   - FastAPI package init
   - Action: MOVE + keep commented stub

2. `src/sqlery/fastapi/app.py` → `src/sqlery/fastapi_sqlery/app.py`
   - FastAPI application (17,845 bytes)
   - Action: MOVE + keep commented stub

3. `src/sqlery/fastapi/backend.py` → `src/sqlery/fastapi_sqlery/backend.py`
   - FastAPI backend (20,596 bytes)
   - Action: MOVE + keep commented stub

4. `src/sqlery/fastapi/cli.py` → `src/sqlery/fastapi_sqlery/cli.py`
   - CLI commands (2,779 bytes)
   - Action: MOVE + keep commented stub

5. `src/sqlery/fastapi/config.py` → `src/sqlery/fastapi_sqlery/config.py`
   - Configuration (2,550 bytes)
   - Action: MOVE + keep commented stub

6. `src/sqlery/fastapi/database.py` → `src/sqlery/fastapi_sqlery/database.py`
   - Database helpers (1,660 bytes)
   - Action: MOVE + keep commented stub

7. `src/sqlery/fastapi/templates/` → `src/sqlery/fastapi_sqlery/templates/`
   - Jinja2 templates
   - Action: MOVE entire directory

**Total**: 6 files + 1 directory = 7 items to migrate

---

## Migration Steps

### Step 1: Rename Directory
```bash
cd ../sqlery-package-split
git mv src/sqlery/fastapi src/sqlery/fastapi_sqlery
```

### Step 2: Create Stubs
For each file, create a commented stub in the original location:
```bash
# Note: No need to create stubs since we're renaming the entire directory
# The directory won't exist in the original location after git mv
```

### Step 3: Update Compatibility Layer
Create `src/sqlery/fastapi_sqlery/__init__.py` with re-exports

### Step 4: Update Documentation
- Update PACKAGE_SPLIT_PLAN.md
- Create FASTAPI_MIGRATION_SUMMARY.md
- Update WORKTREES.md

---

## New FastAPI Structure

```
src/sqlery/fastapi_sqlery/
├── __init__.py              ← Compatibility layer
├── app.py                   ← FastAPI application
├── backend.py               ← FastAPI backend
├── cli.py                   ← CLI commands
├── config.py                ← Configuration
├── database.py              ← Database helpers
└── templates/               ← Jinja2 templates
```

---

## Compatibility Layer

Create `src/sqlery/fastapi_sqlery/__init__.py`:

```python
"""FastAPI integration for sqlery.

This module will be extracted to separate fastapi-sqlery package.

Package naming:
    PyPI package: fastapi-sqlery (with hyphen)
    Import name: fastapi_sqlery (with underscore)
"""

from .app import create_app
from .backend import FastAPIBackend
from .cli import app as cli_app

__all__ = [
    'create_app',
    'FastAPIBackend',
    'cli_app',
]
```

---

## Import Updates

### Before (current)
```python
from sqlery.fastapi.app import create_app
from sqlery.fastapi.backend import FastAPIBackend
```

### After (monorepo)
```python
from sqlery.fastapi_sqlery.app import create_app
from sqlery.fastapi_sqlery.backend import FastAPIBackend
```

### Future (separate package)
```python
from fastapi_sqlery.app import create_app
from fastapi_sqlery.backend import FastAPIBackend
```

---

## Migration Roadmap

### Phase 1: Rename Directory ✅
- Rename `fastapi/` → `fastapi_sqlery/`
- Update imports in moved files

### Phase 2: Update __init__.py
- Create compatibility layer
- Re-export key components

### Phase 3: Update References
- Update pyproject.toml CLI entry points
- Update any core files that reference fastapi

### Phase 4: Test
- Test FastAPI mode still works
- Test CLI commands still work

---

## pyproject.toml Updates Needed

### CLI Entry Points

**Before**:
```toml
[project.scripts]
sqlery = "sqlery.fastapi.cli:app"
sqlery-worker = "sqlery.fastapi.cli:worker_command"
sqlery-web = "sqlery.fastapi.cli:web_command"
```

**After**:
```toml
[project.scripts]
sqlery = "sqlery.fastapi_sqlery.cli:app"
sqlery-worker = "sqlery.fastapi_sqlery.cli:worker_command"
sqlery-web = "sqlery.fastapi_sqlery.cli:web_command"
```

### Optional Dependencies

**Before**:
```toml
[project.optional-dependencies]
fastapi = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
]
```

**After** (keep same, but document it will move):
```toml
# Note: These will move to fastapi-sqlery package in future
fastapi = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
]
```

---

## Future: fastapi-sqlery Package

### Installation
```bash
pip install fastapi-sqlery  # Automatically installs sqlery core
```

### pyproject.toml (separate package)
```toml
[project]
name = "fastapi-sqlery"
version = "1.0.0"
dependencies = [
    "sqlery>=1.0.0",
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
]

[project.scripts]
sqlery = "fastapi_sqlery.cli:app"
sqlery-worker = "fastapi_sqlery.cli:worker_command"
sqlery-web = "fastapi_sqlery.cli:web_command"
```

---

## Comparison: Three Packages

### Core: sqlery
```python
# Pure Python job queue + cron
from sqlery import job, enqueue
from sqlery.backends import create_backend
from sqlery.core.worker import Worker
```

### Django: django-sqlery
```python
# Django ORM integration
from django_sqlery import job, enqueue
from django_sqlery.models import QueuedJob
```

### FastAPI: fastapi-sqlery
```python
# FastAPI web UI + REST API
from fastapi_sqlery import create_app
from fastapi_sqlery.backend import FastAPIBackend
```

---

## Benefits of Split

### For Core Users
- ✅ No FastAPI bloat
- ✅ No Django bloat
- ✅ Minimal dependencies
- ✅ Works anywhere

### For FastAPI Users
- ✅ Clear FastAPI integration
- ✅ Explicit dependency
- ✅ Independent versioning
- ✅ Better documentation

### For Django Users
- ✅ Don't install FastAPI if not needed
- ✅ Cleaner dependency tree

---

**Status**: READY TO EXECUTE
**Next**: Rename fastapi/ → fastapi_sqlery/
