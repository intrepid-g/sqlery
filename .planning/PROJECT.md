# Sqlery — Feature-Complete Run Modes

## What This Is

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and multiple execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The goal is to make every execution mode production-ready across both integration modes, with full test coverage and security hardening.

## Core Value

Every execution mode works reliably and is tested in CI across both Django and standalone integration modes, on both SQLite and PostgreSQL.

## Requirements

### Validated

- ✓ Job enqueueing via `@job` decorator, `.enqueue()`, `.delay()`, `.enqueue_at()` — existing
- ✓ Django ORM backend (`DjangoBackend`) — existing
- ✓ SQLAlchemy/SQLModel standalone backend (`SQLAlchemyBackend`) — existing
- ✓ Daemon mode with worker pool, scheduler, heartbeats, and leases — existing
- ✓ Subprocess execution mode (fork-per-job, memory safe) — existing
- ✓ HTTP trigger middleware (Django, signed internal requests) — existing
- ✓ Lambda/serverless handler with EventBridge integration — existing
- ✓ Job scheduling (cron, interval, one-time) — existing
- ✓ Queue priority, tag concurrency, rate limiting, job dependencies — existing
- ✓ Retry with exponential backoff — existing
- ✓ Optimistic locking (SQLite) / SELECT FOR UPDATE SKIP LOCKED (PostgreSQL) — existing
- ✓ Django admin integration and dashboard — existing
- ✓ FastAPI standalone dashboard and REST API — existing (no auth)
- ✓ CLI tools (Typer-based): sqlery, sqlery-worker, sqlery-web, sqlery-daemon, etc. — existing
- ✓ Alembic migrations for standalone mode — existing
- ✓ RQ and django-tasks-scheduler compatibility layers — existing
- ✓ Database resilience (retry decorator, WAL mode, busy_timeout, connection pooling) — existing

### Active

- [ ] Unify duplicate claiming algorithms (core/claiming.py vs django_sqlery/worker_claiming.py)
- [ ] Unify duplicate execution logic (core/worker.py vs django_sqlery/executor.py)
- [ ] Remove Django imports from framework-agnostic core package (11 of ~18 core modules)
- [ ] Rebuild AsyncWorker with real async backend (asyncpg/aiosqlite via compat layer)
- [ ] Standalone mode parity: all 6 execution modes working in standalone (FastAPI/SQLAlchemy)
- [ ] Full test coverage for every execution mode in both Django and standalone
- [ ] E2E integration tests: enqueue → claim → execute → complete for each mode
- [ ] Edge case tests: timeouts, crashes, retries, concurrent workers, zombie detection
- [ ] Unit tests for every component in every mode
- [ ] PostgreSQL-specific test coverage in CI (beyond the current 2 files)
- [ ] Fix CI workflow triggers (master → main branch)
- [ ] Fix webhook import bug (django_sqlery.webhooks → sqlery.webhooks)
- [ ] Add FastAPI dashboard authentication (API key or basic auth)
- [ ] SSRF protection for webhook URLs (block private/link-local ranges)
- [ ] CSRF fixes for Django admin API endpoints
- [ ] Annotate 21 top-level backward-compat stub files in `src/sqlery/*.py` with a removal-date comment (per dead-code policy — do not delete outright)
- [ ] Mark dead AsyncStorageBackend = None code for removal (comment with deletion date)
- [ ] Mark commented-out code blocks for deletion with dates (don't delete outright — comment-and-date first)
- [ ] All modes passing integration tests in GitHub Actions (SQLite + PostgreSQL)

### Out of Scope

- Mobile app or desktop client — this is a Python library
- Redis/RabbitMQ backends — sqlery is database-backed by design
- Multi-tenancy or SaaS features — library for single-project use
- Graphical monitoring dashboard redesign — existing dashboards get auth, not a rewrite
- New trigger modes beyond the 6 identified — future milestone

## Context

Sqlery originated as a Django-specific task queue (django-sql-jobs) and has been migrating toward a dual-mode architecture where a framework-agnostic core delegates to either a Django or SQLAlchemy backend. The migration is incomplete: the core package still imports Django in 8 of 16 modules, two parallel implementations exist for claiming and execution, and 24 stub files remain from the package reorganization.

The standalone mode (FastAPI/SQLAlchemy) has zero test coverage. The async worker was broken when the backends abstraction layer was removed in v0.13. The CI workflow targets the wrong branch (`master` instead of `main`).

**Six execution modes identified in codebase:**

| Mode | Mechanism | Django | Standalone | Status |
|------|-----------|--------|------------|--------|
| Daemon | Persistent process: scheduler + worker pool + heartbeats | ✓ Works | Partial | Core imports Django |
| Subprocess | Fork-per-job via middleware trigger | ✓ Works | ✗ Missing | Django-only middleware |
| HTTP Trigger | Signed HTTP request triggers subprocess | ✓ Works | ✗ Missing | Django middleware only |
| Lambda/Serverless | AWS Lambda + EventBridge | ✓ Works | ✗ Missing | Django setup_django() |
| Async Worker | asyncio event loop with async backend | ✗ Broken | ✗ Broken | Backend removed in v0.13 |
| Synchronous/Thread | In-process execution (thread strategy) | ✓ Works | ✗ Untested | Via triggers.py |

## Constraints

- **Python version**: 3.10+ minimum (uses `X | None` union syntax)
- **Database**: PostgreSQL (production) or SQLite (dev/lightweight). No other DB engines.
- **Backward compatibility**: Public API (`@job`, `enqueue`, `Queue`) must remain stable
- **Fork safety**: Must handle DB connection lifecycle around `os.fork()` correctly
- **No new dependencies**: Prefer using existing deps (httpx, sqlmodel, asyncio) over adding new ones

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Unify before adding standalone modes | Reduces duplicate work — fix once, test once | — Pending |
| Rebuild async worker (not remove) | User wants all modes production-ready | — Pending |
| Include security fixes in this milestone | Can't ship standalone mode without auth | — Pending |
| Full cleanup (stubs, dead code, comments) | Part of making codebase maintainable | — Pending |
| Mirror all modes in standalone | Standalone should be a first-class citizen | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-12 after initialization*
