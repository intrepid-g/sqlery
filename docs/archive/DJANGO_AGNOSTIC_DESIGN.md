# Django-Agnostic Design Plan

## Vision

Transform `sqlery` into a **dual-mode job queue library** that can operate:
1. **Django Mode** - Full Django integration with admin, middleware, ORM
2. **Standalone Mode** - Pure Python library with web UI, no Django dependency

## Goals

- ✅ **Reduce Django surface area** - Django code only where mandatory
- ✅ **Standalone web UI** - Non-Django equivalent of admin dashboard
- ✅ **Same API** - Identical `@job` decorator and enqueue API in both modes
- ✅ **Database agnostic** - Support PostgreSQL via SQLAlchemy in standalone mode
- ✅ **Easy migration** - Existing Django users unaffected

## Non-Goals

- ❌ **Not removing Django support** - Django mode remains first-class
- ❌ **Not creating two separate packages** - Single package, dual modes
- ❌ **Not supporting other databases** - PostgreSQL only (both modes)

## Architecture Overview

```
sqlery/
├── src/sqlery/
│   ├── core/              # NEW - Django-agnostic core logic
│   │   ├── job.py         # Job decorator, execution logic
│   │   ├── queue.py       # Queue management, job claiming
│   │   ├── worker.py      # Worker process management
│   │   ├── scheduler.py   # Cron scheduling logic
│   │   ├── registry.py    # Job registries (RQ-compatible)
│   │   ├── cleanup.py     # Database retention & cleanup
│   │   ├── models.py      # SQLAlchemy models (for standalone)
│   │   ├── cli.py         # CLI commands (Typer/Click)
│   │   └── daemon.py      # Daemon management logic
│   │
│   ├── django/            # NEW - Django-specific code
│   │   ├── models.py      # Django ORM models
│   │   ├── admin.py       # Django admin integration
│   │   ├── middleware.py  # Django middleware
│   │   ├── views.py       # Django views for dashboard
│   │   └── management/    # Django management commands (wrappers)
│   │
│   ├── standalone/        # NEW - Standalone mode
│   │   ├── app.py         # FastAPI app
│   │   ├── views.py       # Web UI views
│   │   ├── api.py         # REST API endpoints
│   │   └── templates/     # HTML templates (Jinja2)
│   │
│   ├── compat.py          # NEW - Compatibility layer (auto-detect mode)
│   └── __init__.py        # Public API (mode-agnostic)
```

## Implementation Phases

### Phase 1: Core Extraction ✅ COMPLETE

Extract Django-independent logic into `core/` module.

#### Tasks

- [x] **1.1** Create `core/` directory structure
- [x] **1.2** Extract job decorator logic to `core/job.py`
  - Remove Django ORM dependencies
  - Use abstract database interface
- [x] **1.3** Extract queue management to `core/queue.py`
  - Job claiming logic (SELECT FOR UPDATE SKIP LOCKED)
  - Queue priority management
  - Job state transitions
- [x] **1.4** Extract worker logic to `core/worker.py`
  - Worker process management
  - Job execution engine
  - Heartbeat logic
- [x] **1.5** Extract scheduler logic to `core/scheduler.py`
  - Cron expression parsing
  - Scheduled task management
  - Next run time calculation
- [x] **1.6** Extract registry logic to `core/registry.py`
  - RQ-compatible registries
  - Job lifecycle tracking
- [x] **1.7** Extract cleanup logic to `core/cleanup.py`
  - Age-based retention
  - Count-based retention
  - Vacuum operations
- [x] **1.8** Create SQLModel models in `core/models.py` (using Pydantic)
  - `QueuedJob` model with UUID7
  - `ScheduledTask` model
  - `JobRegistry` model
  - `Worker` model with UUID7
- [x] **1.9** Create CLI in `core/cli.py` (Typer-based)
  - daemon start/stop/status/restart
  - workers list/stop
  - cleanup auto/stats/vacuum
  - jobs list
  - init command
- [x] **1.10** Create daemon logic in `core/daemon.py`
  - PID file management
  - Daemonization (fork/detach)
  - Heartbeat tracking
  - Signal handling

**Acceptance Criteria:**
- ✅ All core logic has zero Django imports
- ✅ Core modules can be imported without Django installed
- ✅ SQLModel used for Pydantic-based ORM
- ✅ UUID7 used for Worker IDs
- ✅ Typer CLI provides standalone command interface

**Unified Model Approach:**
- ✅ Created `core/model_schemas.py` with Pydantic schema definitions
- ✅ Created `core/model_utils.py` with utilities for Pydantic → Django model conversion
- ✅ Both SQLModel and Django models synchronized and documented
- ✅ Manual synchronization approach chosen over complex metaprogramming for clarity
- ✅ `model_utils.py` provides foundation for future automatic generation where beneficial

---

### Phase 2: Database Abstraction Layer ✅ COMPLETE

Create abstraction layer to support both Django ORM and SQLAlchemy.

#### Tasks

- [x] **2.1** Create `compat.py` - Abstract database interface
  ```python
  class DatabaseBackend(ABC):
      @abstractmethod
      def create_job(self, **kwargs) -> Job: ...

      @abstractmethod
      def claim_job(self, queues: list[str]) -> Job | None: ...

      @abstractmethod
      def get_scheduled_tasks(self) -> list[ScheduledTask]: ...
  ```

- [x] **2.2** Implement `DjangoBackend` in `django/backend.py`
  - Wraps Django ORM queries
  - Uses Django models
  - All 30+ backend methods implemented

- [x] **2.3** Implement `SQLAlchemyBackend` in `standalone/backend.py`
  - Uses SQLModel/SQLAlchemy
  - Connection pooling via standalone/database.py
  - Transaction management with context managers
  - All 30+ backend methods implemented

- [x] **2.4** Auto-detect mode in `compat.py`
  ```python
  def get_backend() -> DatabaseBackend:
      if 'django' in sys.modules and settings.configured:
          return DjangoBackend()
      else:
          return SQLAlchemyBackend()
  ```

- [x] **2.5** Core modules already use `DatabaseBackend` interface
  - All core modules (job.py, queue.py, worker.py, scheduler.py, etc.) use `get_backend()`
  - Zero Django imports in core modules
  - Backend abstraction complete

**Acceptance Criteria:**
- ✅ Core logic works with both backends
- ✅ Django mode uses Django ORM (existing behavior)
- ✅ Standalone mode uses SQLAlchemy/SQLModel
- ✅ Auto-detection works correctly
- ✅ All 30+ database operations abstracted
- ✅ DjangoConfig and StandaloneConfig implemented

---

### Phase 3: Standalone Web UI ✅ COMPLETE

Build web interface for standalone mode (Django admin equivalent).

#### Technology Choice

**Option A: FastAPI** (Recommended)
- ✅ Modern, async Python 3.13+ features
- ✅ Automatic OpenAPI docs
- ✅ Fast development
- ✅ Built-in dependency injection
- ❌ Requires learning FastAPI

**Option B: Flask**
- ✅ Minimal, familiar
- ✅ Large ecosystem
- ❌ Synchronous by default
- ❌ More boilerplate

**Decision:** FastAPI for better async support and modern patterns.

#### Tasks

- [x] **3.1** Set up FastAPI application in `standalone/app.py`
  ```python
  from fastapi import FastAPI
  from fastapi.staticfiles import StaticFiles
  from fastapi.templating import Jinja2Templates

  app = FastAPI(title="sqlery Dashboard")
  ```

- [x] **3.2** Create HTML templates in `standalone/templates/`
  - [x] `base.html` - Base layout with Tailwind CSS and Alpine.js navigation
  - [x] `dashboard.html` - Overview dashboard with statistics and auto-refresh
  - [x] `jobs_list.html` - List all jobs with filters (pagination TODO)
  - [x] `job_detail.html` - Job detail view with full information
  - [x] `scheduled_tasks.html` - Scheduled tasks management
  - [x] `workers.html` - Worker status and management
  - [x] `registries.html` - Job registries view (RQ-compatible)
  - [x] `error.html` - Error page

- [x] **3.3** Create REST API endpoints in `standalone/app.py`
  - [x] `GET /api/jobs` - List jobs with filters
  - [x] `GET /api/jobs/{id}` - Job details
  - [x] `DELETE /api/jobs/{id}` - Cancel job
  - [x] `GET /api/scheduled-tasks` - List scheduled tasks
  - [x] `GET /api/workers` - Worker status
  - [x] `GET /api/stats` - Dashboard statistics
  - [x] `GET /health` - Health check endpoint
  - [ ] `POST /api/jobs` - Create manual job (TODO)
  - [ ] `POST /api/scheduled-tasks` - Create scheduled task (TODO)
  - [ ] `PUT /api/scheduled-tasks/{id}` - Update scheduled task (TODO)
  - [ ] `DELETE /api/scheduled-tasks/{id}` - Delete scheduled task (TODO)
  - [ ] `POST /api/workers/stop` - Stop workers (TODO)

- [x] **3.4** HTML view handlers integrated in `standalone/app.py`
  - [x] Dashboard view - stats, workers, running jobs
  - [x] Jobs list view with filters
  - [x] Job detail view - complete job information
  - [x] Scheduled tasks view
  - [x] Workers view
  - [x] Registries view

- [x] **3.5** Add static assets
  - [x] Tailwind CSS (via CDN)
  - [x] Alpine.js for interactivity (via CDN)
  - [x] Auto-refresh for dashboard (every 3 seconds)
  - [x] Responsive design for mobile/tablet/desktop

- [ ] **3.6** Add authentication/authorization (TODO - Future)
  - [ ] Basic auth for development
  - [ ] Token-based auth for production
  - [ ] Role-based access control (optional)

**Acceptance Criteria:**
- ✅ Standalone web UI has core feature parity with Django admin
- ✅ Dashboard auto-refreshes every 3 seconds
- ✅ UI is responsive and modern (Tailwind CSS)
- ✅ Can run on `http://localhost:8000` without Django
- ✅ All main views implemented (dashboard, jobs, tasks, workers, registries)
- ⏳ CRUD operations for manual job creation (TODO)
- ⏳ Authentication (TODO - future enhancement)

---

### Phase 4: Configuration & Initialization ⏳

Unified configuration for both modes.

#### Tasks

- [ ] **4.1** Create `core/config.py` - Configuration management
  ```python
  class Config:
      # Database
      DATABASE_URL: str = "postgresql://..."

      # Worker settings
      MAX_WORKERS_PER_NODE: int = 3
      WORKER_QUEUES: list[str] = ['default']
      QUEUE_PRIORITIES: dict[str, int] = {'default': 50}

      # Daemon settings
      ENABLE_DAEMON: bool = True
      DAEMON_CHECK_INTERVAL: int = 10

      # Retention settings
      AUTO_CLEANUP_JOBS: bool = False
      JOB_RETENTION: dict = {...}
  ```

- [ ] **4.2** Support multiple config sources
  - [ ] Environment variables
  - [ ] Config file (YAML/TOML)
  - [ ] Django settings (when in Django mode)
  - [ ] Programmatic configuration

- [ ] **4.3** Create initialization API
  ```python
  # Django mode (auto-detected)
  # No initialization needed, uses Django settings

  # Standalone mode
  from sqlery import initialize

  initialize(
      database_url="postgresql://localhost/jobs",
      max_workers=3,
      enable_daemon=True
  )
  ```

- [ ] **4.4** Update `__init__.py` to export clean API
  ```python
  # Core API (works in both modes)
  from .core.job import job
  from .core.queue import enqueue, enqueue_at

  # Initialization (standalone only)
  from .compat import initialize

  # Web UI (standalone only)
  from .fastapi.app import app
  ```

**Acceptance Criteria:**
- Django mode works without any initialization code
- Standalone mode requires explicit `initialize()` call
- Configuration precedence is clear and documented
- All existing Django users unaffected

---

### Phase 5: Management Commands (CLI) ⏳

Create CLI commands in core (already part of Phase 1).

#### Tasks

- [ ] **5.1** CLI already in `core/cli.py` (from Phase 1)
  ```bash
  # Django mode (existing)
  python manage.py daemon start
  python manage.py workers list

  # Standalone mode (new)
  sql-jobs daemon start
  sql-jobs workers list
  ```

- [ ] **5.2** Implement commands
  - [ ] `sql-jobs daemon start` - Start daemon
  - [ ] `sql-jobs daemon stop` - Stop daemon
  - [ ] `sql-jobs daemon status` - Check daemon status
  - [ ] `sql-jobs workers list` - List workers
  - [ ] `sql-jobs workers stop` - Stop all workers
  - [ ] `sql-jobs cleanup auto` - Run cleanup
  - [ ] `sql-jobs cleanup stats` - Show cleanup stats
  - [ ] `sql-jobs migrate` - Run database migrations

- [ ] **5.3** Create entry point in `pyproject.toml`
  ```toml
  [project.scripts]
  sql-jobs = "sqlery.fastapi.cli:main"
  ```

**Acceptance Criteria:**
- All Django management commands have standalone CLI equivalents
- CLI works without Django installed
- Help text is clear and comprehensive

---

### Phase 6: Database Migrations ⏳

Support migrations in both modes.

#### Tasks

- [ ] **6.1** Keep Django migrations as-is
  - Existing Django users use `python manage.py migrate`

- [ ] **6.2** Create Alembic migrations for standalone mode
  - [ ] Set up Alembic configuration
  - [ ] Create initial migration matching Django schema
  - [ ] Add migration command: `sql-jobs migrate`

- [ ] **6.3** Ensure schema compatibility
  - Both modes use identical PostgreSQL schema
  - Users can switch between modes seamlessly

**Acceptance Criteria:**
- Django migrations work as before
- Standalone mode uses Alembic for migrations
- Both produce identical database schema
- Migration from Django to standalone (and vice versa) is safe

---

### Phase 7: Documentation ⏳

Update all documentation for dual-mode operation.

#### Tasks

- [ ] **7.1** Update `README.md`
  - [ ] Add "Standalone Mode" section
  - [ ] Update installation instructions
  - [ ] Add standalone quick start
  - [ ] Show both Django and standalone examples

- [ ] **7.2** Create `STANDALONE_MODE.md`
  - [ ] Installation guide
  - [ ] Configuration reference
  - [ ] Web UI documentation
  - [ ] CLI reference
  - [ ] Deployment guide (Docker, systemd)

- [ ] **7.3** Update `CONFIGURATION.md`
  - [ ] Add standalone configuration examples
  - [ ] Document config file format
  - [ ] Environment variable reference

- [ ] **7.4** Create migration guide `DJANGO_TO_STANDALONE.md`
  - [ ] When to use standalone mode
  - [ ] How to migrate Django project to standalone
  - [ ] Schema compatibility notes

- [ ] **7.5** Update comparison tables
  - [ ] Show sqlery now works without Django
  - [ ] Compare to other non-Django solutions (RQ, Celery)

**Acceptance Criteria:**
- All documentation covers both modes
- Examples show Django and standalone usage
- Migration path is clear

---

### Phase 8: Testing ⏳

Comprehensive test coverage for both modes.

#### Tasks

- [ ] **8.1** Set up dual test environments
  - [ ] `tests/django/` - Django mode tests
  - [ ] `tests/standalone/` - Standalone mode tests
  - [ ] `tests/core/` - Core logic tests (mode-agnostic)

- [ ] **8.2** Test Django mode
  - [ ] All existing tests still pass
  - [ ] Django admin integration works
  - [ ] Django middleware works

- [ ] **8.3** Test standalone mode
  - [ ] Core logic works without Django
  - [ ] Web UI works
  - [ ] API endpoints work
  - [ ] CLI commands work
  - [ ] Database migrations work

- [ ] **8.4** Test mode switching
  - [ ] Can import in non-Django environment
  - [ ] Auto-detection works correctly
  - [ ] No Django import errors in standalone

- [ ] **8.5** Integration tests
  - [ ] Multi-worker in standalone mode
  - [ ] Job registries in standalone mode
  - [ ] Cleanup in standalone mode
  - [ ] Daemon in standalone mode

**Acceptance Criteria:**
- >90% test coverage for both modes
- All tests pass in CI for both modes
- No regressions for Django users

---

### Phase 9: Packaging & Dependencies ⏳

Update package configuration for optional Django dependency.

#### Tasks

- [ ] **9.1** Update `pyproject.toml`
  ```toml
  [project]
  name = "sqlery"
  dependencies = [
      "sqlalchemy>=2.0",
      "psycopg2-binary>=2.9",
      "croniter>=1.0",
  ]

  [project.optional-dependencies]
  django = [
      "django>=4.2",
  ]
  standalone = [
      "fastapi>=0.104",
      "uvicorn[standard]>=0.24",
      "jinja2>=3.1",
      "alembic>=1.12",
      "typer>=0.9",
  ]
  all = [
      "sqlery[django,standalone]"
  ]
  ```

- [ ] **9.2** Update installation docs
  ```bash
  # Django mode
  pip install sqlery[django]

  # Standalone mode
  pip install sqlery[standalone]

  # Both modes
  pip install sqlery[all]
  ```

- [ ] **9.3** Test installation in isolated environments
  - [ ] Django-only install works
  - [ ] Standalone-only install works
  - [ ] No conflicting dependencies

**Acceptance Criteria:**
- Django is optional dependency
- Standalone mode has no Django requirement
- Installation instructions are clear

---

### Phase 10: Deployment Examples ⏳

Provide deployment examples for standalone mode.

#### Tasks

- [ ] **10.1** Create Docker example for standalone mode
  ```dockerfile
  FROM python:3.13-slim
  RUN pip install sqlery[standalone]
  CMD ["sql-jobs", "daemon", "start"]
  ```

- [ ] **10.2** Create docker-compose example
  ```yaml
  services:
    db:
      image: postgres:17
    jobs:
      image: sqlery:latest
      depends_on: [db]
    web:
      image: sqlery:latest
      command: uvicorn sqlery.fastapi.app:app
      ports: ["8000:8000"]
  ```

- [ ] **10.3** Create systemd service example
  ```ini
  [Service]
  ExecStart=/usr/local/bin/sql-jobs daemon start
  ```

- [ ] **10.4** Create Kubernetes deployment example

- [ ] **10.5** Add to `STANDALONE_MODE.md` deployment section

**Acceptance Criteria:**
- All deployment examples tested and working
- Examples cover common deployment scenarios
- Documentation includes troubleshooting

---

## Success Metrics

### Functional Requirements
- ✅ Standalone mode works without Django installed
- ✅ Django mode continues to work unchanged
- ✅ Feature parity between modes (99% of features)
- ✅ Same API in both modes (`@job`, `enqueue()`)

### Non-Functional Requirements
- ✅ Zero performance regression for Django users
- ✅ Clean separation of concerns (core vs Django vs standalone)
- ✅ Comprehensive test coverage (>90%)
- ✅ Clear documentation for both modes

### Adoption Metrics
- ✅ Existing Django users can upgrade seamlessly
- ✅ New standalone users can get started in <5 minutes
- ✅ Migration from Django to standalone (or vice versa) is documented

---

## Risk Analysis

### Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Breaking changes for Django users** | HIGH | Extensive testing, maintain backward compatibility |
| **SQLAlchemy model mismatch** | MEDIUM | Schema compatibility tests, identical field definitions |
| **Performance difference between ORMs** | MEDIUM | Benchmark both modes, optimize if needed |
| **Complexity increases** | MEDIUM | Clear module separation, good documentation |

### Maintenance Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Two codepaths to maintain** | HIGH | Maximize code reuse via core/ abstraction |
| **Two web UIs to maintain** | HIGH | Share templates/logic where possible |
| **Migration drift** | MEDIUM | Automated schema comparison tests |

---

## Timeline Estimate

| Phase | Estimated Time | Priority |
|-------|---------------|----------|
| Phase 1: Core Extraction | 2-3 days | HIGH |
| Phase 2: Database Abstraction | 2-3 days | HIGH |
| Phase 3: Standalone Web UI | 3-4 days | HIGH |
| Phase 4: Configuration | 1-2 days | HIGH |
| Phase 5: CLI Commands | 1-2 days | MEDIUM |
| Phase 6: Migrations | 1-2 days | MEDIUM |
| Phase 7: Documentation | 2-3 days | MEDIUM |
| Phase 8: Testing | 2-3 days | HIGH |
| Phase 9: Packaging | 1 day | LOW |
| Phase 10: Deployment Examples | 1-2 days | LOW |

**Total Estimated Time:** 16-25 days (3-5 weeks)

---

## Open Questions

1. **Q:** Should we rename the package to remove "django" from the name?
   - **A:** No, keep the name for SEO and existing user recognition. Clarify in docs that it works standalone.

2. **Q:** Should standalone mode support SQLite in addition to PostgreSQL?
   - **A:** No, PostgreSQL-only for both modes. `SELECT FOR UPDATE SKIP LOCKED` requires PostgreSQL.

3. **Q:** Should we provide a migration tool to convert Django projects to standalone?
   - **A:** Yes, add `sql-jobs convert-from-django` command in Phase 5.

4. **Q:** How do we handle Django-specific features like `select_related()` optimization?
   - **A:** Document performance characteristics of both backends. Optimize SQLAlchemy queries separately.

5. **Q:** Should the standalone web UI match Django admin aesthetics?
   - **A:** No, create modern UI with Tailwind CSS. Django admin is dated.

---

## Next Steps

1. Review this design document
2. Get feedback on technology choices (FastAPI vs Flask)
3. Start with Phase 1: Core Extraction
4. Create separate branch `feature/standalone-mode` for development
5. Implement phases incrementally with tests

---

**Document Status:** Draft
**Created:** 2025-10-17
**Last Updated:** 2025-10-17
**Version:** 1.0
