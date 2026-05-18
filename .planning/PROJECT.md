# Sqlery — Stability, Coverage, and Operational Confidence

## What This Is

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and six execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The current milestone is about making those modes boring to operate: trustworthy CI signal, battle-tested failure handling, and operator-grade docs before expanding the product surface further.

## Core Value

Every execution mode works reliably and is tested in CI across both Django and standalone integrations, on both SQLite and PostgreSQL, with operational guidance that maintainers can trust in production.

## Current State

**Shipped:** v0.21 — Feature-Complete Run Modes (2026-05-15). All 6 execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous) are production-ready in both Django and standalone integrations, tested on SQLite and Postgres. Async worker was rebuilt with real async backends plus drain-with-deadline shutdown. Security was hardened with dashboard auth, webhook SSRF defense, CSRF protection, and an opt-in task module allowlist. Archive: `.planning/milestones/v0.21-*`.

## Current Milestone: v0.22 Stability, Coverage, and Operational Confidence

**Goal:** Raise confidence in the existing six execution modes before adding new compatibility surface area by fixing CI signal, hardening failure/concurrency behavior, and closing the highest-value operational gaps.

**Target features:**
- Eliminate the temporary coverage/collection workaround and make CI signal trustworthy
- Add battle-testing for crashes, retries, heartbeats, leases, concurrency, and fork/DB lifecycle
- Verify PostgreSQL-heavy operational behavior with stronger regression coverage
- Close the highest-value operator and security hardening gaps that affect production trust
- Improve runbooks and troubleshooting docs for real deployment/recovery workflows

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

- [ ] Test suite runs without the known collection-error workaround that kept coverage pinned to a temporary 13% floor
- [ ] CI signal is trustworthy for default and PostgreSQL rails, including the standalone-no-Django path
- [ ] Failure handling is battle-tested for daemon/worker crash, retry, timeout, zombie, heartbeat, and lease-recovery paths
- [ ] PostgreSQL concurrency and claim/lease behavior have stronger regression coverage under multi-worker scenarios
- [ ] Operator docs cover deploy, run, observe, recover, and troubleshoot flows for the production-facing modes
- [ ] The most important documented follow-up hardening gaps are either closed or explicitly deferred with evidence

### Out of Scope

- Mobile app or desktop client — this is a Python library
- Redis/RabbitMQ backends — sqlery is database-backed by design
- Multi-tenancy or SaaS features — library for single-project use
- Graphical monitoring dashboard redesign — existing dashboards get auth, not a rewrite
- New trigger modes beyond the 6 identified — future milestone

## Context

Sqlery originated as a Django-specific task queue (django-sql-jobs) and has been migrating toward a dual-mode architecture where a framework-agnostic core delegates to either a Django or SQLAlchemy backend. The execution-mode milestone is now complete and archived, but the audit and backlog still show confidence gaps: coverage is temporarily pinned to a low floor because of test-collection issues, one CI human-verify item remains open, Lambda fidelity is only smoke-tested, and there are follow-up hardening items in webhook SSRF validation and operator guidance.

This milestone deliberately favors maturity over feature expansion. The compatibility milestone remains strategically important, but it moves behind a trust-building pass so the current system is easier to operate, verify, and evolve.

## Constraints

- **Python version**: 3.10+ minimum (uses `X | None` union syntax)
- **Database**: PostgreSQL (production) or SQLite (dev/lightweight). No other DB engines.
- **Backward compatibility**: Public API (`@job`, `enqueue`, `Queue`) must remain stable
- **Fork safety**: Must handle DB connection lifecycle around `os.fork()` correctly
- **No new dependencies**: Prefer using existing deps (httpx, sqlmodel, asyncio) over adding new ones

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Maturity before expansion** (2026-05-15) | After shipping all six execution modes in v0.21, the highest-value next step is to raise confidence in CI, battle-test failure paths, and improve operational guidance before adding permanent new compatibility surface area. | — Locked for v0.22 |
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
*Last updated: 2026-05-15 after milestone v0.22 reset*
