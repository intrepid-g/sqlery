# Sqlery — Feature-Complete Run Modes

## What This Is

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and multiple execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The goal is to make every execution mode production-ready across both integration modes, with full test coverage and security hardening.

## Core Value

Every execution mode works reliably and is tested in CI across both Django and standalone integration modes, on both SQLite and PostgreSQL.

## Current State

**Shipped:** v0.21 — Feature-Complete Run Modes (2026-05-15). All 6 execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous) production-ready in both Django and standalone integrations, tested on SQLite and Postgres. Async worker rebuilt with real async backends + drain-with-deadline shutdown. Security hardened: three-mode dashboard auth, webhook SSRF defense, CSRF protection, opt-in task module allowlist. 43/43 v0.21 requirements verified. Archive: `.planning/milestones/v0.21-*`.

## Next Milestone Goals

Top-priority direction (per `.planning/BACKLOG.md`): **drop-in compatibility is a permanent first-class feature** — users migrating from Celery, RQ, or django-tasks-scheduler should change only their import paths. Compat shims stay forever (reverses the v3.2.0 removal note in `compat/rq.py`).

Next milestone work (one focused milestone, ~6-8 plans):
1. New `sqlery.compat.celery` module — currently missing. Mirror Celery's `@app.task` / `@shared_task` decorators, `.delay()`, `.apply_async()`, `AsyncResult` API.
2. De-deprecate `sqlery.compat.rq`; complete RQ public-API parity audit.
3. Verify/audit `sqlery.compat.scheduler` for the django-tasks-scheduler `@job` decorator.
4. Contract tests — each compat module exercises a representative slice of the original library's public API.
5. Migration guide docs.

Carry-forward `[FOLLOWUP]` items from v0.21 (lower priority): coverage gate 13→70%, Phase 1 CI human-verify push, Lambda fidelity (LocalStack/SAM), SSRF v2 hardening, quarterly dead-code retention sweep. See `.planning/BACKLOG.md` for the full list.

Start the next milestone with `/gsd-new-milestone`.

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
| **Drop-in compatibility is a permanent first-class feature** (2026-05-15) | Users migrating from Celery, RQ, or django-tasks-scheduler should change only their import paths. Compat shims (`sqlery.compat.celery`, `sqlery.compat.rq`, `sqlery.compat.scheduler`) are NOT transitional — they stay forever. Means: every public decorator/queue/job API in those libraries needs a sqlery-backed equivalent. Reverses the "Deprecated since v3.1.0 — will be removed in v3.2.0" notes in `compat/rq.py`. | — Locked, implementation pending |

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
