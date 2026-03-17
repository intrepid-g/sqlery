# Git Worktrees Tracking

This file tracks active git worktrees for the sqlery project.

## Active Worktrees

*No active worktrees*

## Completed Worktrees

### feature/package-split ✅ MERGED
- **Path**: `../sqlery-package-split` (removed)
- **Branch**: `feature/package-split`
- **Created**: 2025-11-12
- **Merged**: 2025-11-12 (commit `53d9adb`)
- **Purpose**: Prepare framework-specific code separation for future package extraction
- **Status**: ✅ MERGED AND REMOVED
- **Goal**: Make Django and FastAPI code ready to extract to separate packages
- **Strategy**:
  - Move Django files to `django_sqlery/` subfolder (26 files + 3 dirs)
  - Move FastAPI files to `fastapi_sqlery/` subfolder (6 files + 1 dir)
  - Comment out Django code (not delete) with `#CLEANUP` markers
  - Rename FastAPI directory (cleaner than stubs)
  - Keep all modes working with backward compatibility
  - Update pyproject.toml CLI entry points
- **Related**: See `PACKAGE_SPLIT_PLAN.md` for full strategy
- **Commits**:
  - `feef24e` - Move Django code to django/ subfolder
  - `9b0cbbf` - Rename django/ → django_sqlery/ for package consistency
  - `d7e8b39` - Move FastAPI code to fastapi_sqlery/
  - `53d9adb` - Merged to master

---

### What Was Migrated

#### Django Integration → `src/sqlery/django_sqlery/`
**Package**: Will become `django-sqlery` (import as `django_sqlery`)
**Files**: 26 Python files + management/ + migrations/ + templates/
**Strategy**: Commented stubs with #CLEANUP markers in original locations
**Size**: ~10,000+ lines of code

**Files moved**:
- ORM & Admin: models.py, admin.py, apps.py, urls.py, views.py
- Integration: decorators.py, queue.py, executor.py, settings.py, db_compat.py
- Worker: 8 worker-related files
- Dashboard: dashboard_views.py, registries.py, middleware.py, http_trigger_middleware.py
- Utilities: cleanup.py
- Directories: management/commands/, migrations/, templates/

#### FastAPI Integration → `src/sqlery/fastapi_sqlery/`
**Package**: Will become `fastapi-sqlery` (import as `fastapi_sqlery`)
**Files**: 6 Python files + templates/
**Strategy**: Renamed entire directory (no stubs needed)
**Size**: ~47KB of code

**Files moved**:
- app.py (FastAPI application)
- backend.py (FastAPI backend)
- cli.py (CLI commands)
- config.py (Configuration)
- database.py (Database helpers)
- templates/ (Jinja2 templates)

**CLI entry points updated** in pyproject.toml:
- `sqlery` command
- `sqlery-worker` command
- `sqlery-web` command

---

### Import Migration Paths

**Before (monorepo, original)**:
```python
from sqlery.models import QueuedJob              # Django
from sqlery.decorators import job                # Django
from sqlery.fastapi.app import create_app       # FastAPI
```

**Current (monorepo, migrated)**:
```python
from sqlery.django_sqlery.models import QueuedJob    # Django
from sqlery.django_sqlery.decorators import job      # Django
from sqlery.fastapi_sqlery.app import create_app    # FastAPI
```

**Future (separate packages)**:
```python
from django_sqlery.models import QueuedJob       # django-sqlery package
from django_sqlery.decorators import job         # django-sqlery package
from fastapi_sqlery.app import create_app       # fastapi-sqlery package
```

---

### Core Package Remains Clean

After migration, the core `sqlery` package contains only framework-agnostic code:

✅ `backends/` - Database backends (SQLite, PostgreSQL)
✅ `core/` - Core job queue logic
✅ `schema.py` - SQL schema
✅ `utils.py` - Utilities
✅ `rate_limit_utils.py` - Rate limiting

📦 `django_sqlery/` - Django integration (to be extracted)
📦 `fastapi_sqlery/` - FastAPI integration (to be extracted)

## Worktree Commands

```bash
# List all worktrees
git worktree list

# Switch to worktree
cd ../sqlery-package-split

# Remove worktree when done
git worktree remove ../sqlery-package-split

# Or if worktree is broken
git worktree prune
```

## Notes

- Always commit in worktree before removing
- Merge feature branch back to master when ready
- Clean up worktrees regularly to avoid confusion
