# Django Code Migration Plan

**Branch**: feature/package-split
**Goal**: Move Django-specific code to `src/sqlery/django_sqlery/` subfolder
**Strategy**: Comment out (not delete) Django code, add #CLEANUP markers

---

## Files to Move to `src/sqlery/django_sqlery/`

### Category 1: Django ORM & Admin (HIGH PRIORITY)

**Source → Destination**

1. `src/sqlery/models.py` → `src/sqlery/django_sqlery/models.py`
   - Django ORM models: QueuedJob, ScheduledTask, Worker
   - Size: Large (~1000+ lines)
   - Action: MOVE + keep commented stub in original location

2. `src/sqlery/admin.py` → `src/sqlery/django_sqlery/admin.py`
   - Django admin configuration
   - Action: MOVE + keep commented stub

3. `src/sqlery/apps.py` → `src/sqlery/django_sqlery/apps.py`
   - Django app config
   - Action: MOVE + keep commented stub

4. `src/sqlery/urls.py` → `src/sqlery/django_sqlery/urls.py`
   - Django URL patterns
   - Action: MOVE + keep commented stub

5. `src/sqlery/views.py` → `src/sqlery/django_sqlery/views.py`
   - Django views
   - Action: MOVE + keep commented stub

### Category 2: Django Management Commands

**Source → Destination**

6. `src/sqlery/management/` → `src/sqlery/django_sqlery/management/`
   - All management commands
   - Action: MOVE entire directory

### Category 3: Django Migrations

**Source → Destination**

7. `src/sqlery/migrations/` → `src/sqlery/django_sqlery/migrations/`
   - All migration files
   - Action: MOVE entire directory

### Category 4: Django Integration Layer

**Source → Destination**

8. `src/sqlery/decorators.py` → `src/sqlery/django_sqlery/decorators.py`
   - @job decorator (Django-specific)
   - Action: MOVE + keep commented stub

9. `src/sqlery/queue.py` → `src/sqlery/django_sqlery/queue.py`
   - enqueue() using Django ORM
   - Action: MOVE + keep commented stub

10. `src/sqlery/executor.py` → `src/sqlery/django_sqlery/executor.py`
    - TaskExecutor using Django ORM
    - Action: MOVE + keep commented stub

11. `src/sqlery/settings.py` → `src/sqlery/django_sqlery/settings.py`
    - Django settings integration
    - Action: MOVE + keep commented stub

12. `src/sqlery/db_compat.py` → `src/sqlery/django_sqlery/db_compat.py`
    - Django database compatibility
    - Action: MOVE + keep commented stub

### Category 5: Django Worker Process

**Source → Destination**

13. `src/sqlery/worker_process.py` → `src/sqlery/django_sqlery/worker_process.py`
    - Django worker process
    - Action: MOVE + keep commented stub

14. `src/sqlery/worker_registry.py` → `src/sqlery/django_sqlery/worker_registry.py`
    - Worker registration (Django)
    - Action: MOVE + keep commented stub

15. `src/sqlery/worker_claiming.py` → `src/sqlery/django_sqlery/worker_claiming.py`
    - Worker job claiming (Django)
    - Action: MOVE + keep commented stub

16. `src/sqlery/daemon_worker.py` → `src/sqlery/django_sqlery/daemon_worker.py`
    - Daemon worker (Django)
    - Action: MOVE + keep commented stub

17. `src/sqlery/daemon_manager.py` → `src/sqlery/django_sqlery/daemon_manager.py`
    - Daemon manager (Django)
    - Action: MOVE + keep commented stub

18. `src/sqlery/daemon_middleware.py` → `src/sqlery/django_sqlery/daemon_middleware.py`
    - Daemon middleware (Django)
    - Action: MOVE + keep commented stub

19. `src/sqlery/subprocess_executor.py` → `src/sqlery/django_sqlery/subprocess_executor.py`
    - Subprocess executor (Django)
    - Action: MOVE + keep commented stub

20. `src/sqlery/subprocess_middleware.py` → `src/sqlery/django_sqlery/subprocess_middleware.py`
    - Subprocess middleware (Django)
    - Action: MOVE + keep commented stub

### Category 6: Django Dashboard & Views

**Source → Destination**

21. `src/sqlery/dashboard_views.py` → `src/sqlery/django_sqlery/dashboard_views.py`
    - Django dashboard views
    - Action: MOVE + keep commented stub

22. `src/sqlery/registries.py` → `src/sqlery/django_sqlery/registries.py`
    - Django-specific registries (uses Django cache)
    - Action: MOVE + keep commented stub

23. `src/sqlery/middleware.py` → `src/sqlery/django_sqlery/middleware.py`
    - Django middleware
    - Action: MOVE + keep commented stub

24. `src/sqlery/http_trigger_middleware.py` → `src/sqlery/django_sqlery/http_trigger_middleware.py`
    - HTTP trigger middleware (Django)
    - Action: MOVE + keep commented stub

### Category 7: Django Templates

**Source → Destination**

25. `src/sqlery/templates/` → `src/sqlery/django_sqlery/templates/`
    - Django admin templates
    - Action: MOVE entire directory

### Category 8: Django Utilities

**Source → Destination**

26. `src/sqlery/cleanup.py` → `src/sqlery/django_sqlery/cleanup.py`
    - Cleanup utilities (uses Django ORM)
    - Action: MOVE + keep commented stub

---

## Files to Keep in Core (No Django Dependencies)

These files stay where they are:

✅ `src/sqlery/backends/` - All files (no Django)
✅ `src/sqlery/core/` - All files (no Django)
✅ `src/sqlery/fastapi/` - All files (FastAPI mode)
✅ `src/sqlery/schema.py` - Pure SQL
✅ `src/sqlery/utils.py` - Utilities (check for Django imports)
✅ `src/sqlery/rate_limit_utils.py` - Rate limiting (no Django)

---

## Stub Pattern (Commented Code)

For each moved file, leave a stub in the original location:

```python
# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/models.py
# This stub exists for backward compatibility during migration
# TODO: Remove this file when django-sqlery is extracted to separate package

# Original code commented out below:
# ============================================================================

# from django.db import models
# from django.utils import timezone
# ...
# (rest of original code)

# ============================================================================
# End of commented code
```

---

## Import Updates

### In Django Files

**Before:**
```python
from sqlery.models import QueuedJob
from sqlery.executor import TaskExecutor
```

**After:**
```python
from sqlery.django.models import QueuedJob
from sqlery.django.executor import TaskExecutor
```

### In Core Files

Core files should NOT import from django/:
```python
# ❌ BAD - core should not import django
from sqlery.django.models import QueuedJob

# ✅ GOOD - django can import from core
# (in django files only)
from sqlery.core.job import JobWrapper
from sqlery.backends import get_backend
```

---

## Migration Steps

### Phase 1: Prepare Django Folder Structure
- [ ] Create subdirectories in `src/sqlery/django_sqlery/`
  - [ ] `management/commands/`
  - [ ] `migrations/`
  - [ ] `templates/`

### Phase 2: Move Files (Category by Category)
- [ ] Category 1: ORM & Admin
- [ ] Category 2: Management Commands
- [ ] Category 3: Migrations
- [ ] Category 4: Integration Layer
- [ ] Category 5: Worker Process
- [ ] Category 6: Dashboard & Views
- [ ] Category 7: Templates
- [ ] Category 8: Utilities

### Phase 3: Update Imports
- [ ] Update imports in moved Django files
- [ ] Update any core files that reference moved files
- [ ] Add compatibility imports in `src/sqlery/django_sqlery/__init__.py`

### Phase 4: Create Stubs
- [ ] Add commented stubs in original file locations
- [ ] Add #CLEANUP markers
- [ ] Add TODO notes

### Phase 5: Test
- [ ] Run Django tests
- [ ] Run standalone tests
- [ ] Verify both modes work

### Phase 6: Document
- [ ] Update README
- [ ] Add migration notes
- [ ] Document new import paths

---

## Compatibility Layer

Create `src/sqlery/django_sqlery/__init__.py` with backward-compatible imports:

```python
"""Django integration for sqlery.

This module will eventually be extracted to a separate django-sqlery package.
"""

# Re-export Django-specific components for backward compatibility
try:
    from .models import QueuedJob, ScheduledTask, Worker
    from .decorators import job
    from .queue import enqueue, enqueue_at
    from .executor import TaskExecutor

    __all__ = [
        'QueuedJob',
        'ScheduledTask',
        'Worker',
        'job',
        'enqueue',
        'enqueue_at',
        'TaskExecutor',
    ]
except ImportError:
    # Django not installed
    pass
```

---

## Testing Strategy

1. **Before migration**:
   - Run full test suite: `pytest tests/`
   - Record results

2. **After each category migration**:
   - Run Django tests
   - Run standalone tests
   - Fix any import errors

3. **After all migrations**:
   - Run full test suite again
   - Compare with before results
   - All tests should still pass

---

## Rollback Plan

If something breaks:
1. Git worktree makes rollback easy
2. Can discard branch: `git worktree remove`
3. Original master branch untouched

---

**Status**: READY TO START
**Next**: Begin Phase 1 - Prepare Django folder structure
