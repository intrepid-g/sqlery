# Roadmap: Sqlery Feature-Complete Run Modes

## Overview

Sqlery's dual-mode architecture (Django + Standalone) has diverged into parallel implementations with incomplete standalone support and a broken async worker. This roadmap unifies the codebase first, then builds out all six execution modes across both integration paths, validates everything with comprehensive tests, and hardens with security and cleanup. Four phases, each delivering a coherent capability that unblocks the next.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Core Unification** - Consolidate duplicate code and remove Django imports from framework-agnostic core
- [ ] **Phase 2: Execution Modes & Async Rebuild** - All 6 execution modes working in both Django and standalone, with rebuilt async worker
- [ ] **Phase 3: Testing & CI** - Full test coverage for every mode in both integration paths, CI fixed
- [ ] **Phase 4: Security & Cleanup** - Authentication, SSRF/CSRF protection, and dead code cleanup

## Phase Details

### Phase 1: Core Unification
**Goal**: The core package is truly framework-agnostic -- it runs without Django installed and serves as the single source of truth for claiming and execution logic
**Depends on**: Nothing (first phase)
**Requirements**: UNIF-01, UNIF-02, UNIF-03, UNIF-04, UNIF-05, UNIF-06
**Success Criteria** (what must be TRUE):
  1. Running `python -c "import sqlery.core"` succeeds in a virtualenv without Django installed
  2. `core/claiming.py` is the sole claiming implementation -- `django_sqlery/worker_claiming.py` delegates to it
  3. `core/worker.py` JobExecutor is the sole execution engine -- `django_sqlery/executor.py` delegates to it
  4. `core/daemon.py` and `core/db_resilience.py` function correctly when Django is not installed
**Plans**: TBD
**UI hint**: no

### Phase 2: Execution Modes & Async Rebuild
**Goal**: All six execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous/thread) pass end-to-end tests in both Django and standalone integration modes
**Depends on**: Phase 1
**Requirements**: DMOD-01, DMOD-02, DMOD-03, DMOD-04, DMOD-05, DMOD-06, SMOD-01, SMOD-02, SMOD-03, SMOD-04, SMOD-05, SMOD-06, ASYN-01, ASYN-02, ASYN-03, ASYN-04, ASYN-05
**Success Criteria** (what must be TRUE):
  1. Each of the 6 execution modes completes a full enqueue-claim-execute-complete cycle in Django
  2. Each of the 6 execution modes completes a full enqueue-claim-execute-complete cycle in standalone (FastAPI/SQLAlchemy)
  3. AsyncWorker uses a real async database backend (asyncpg for PostgreSQL, aiosqlite for SQLite) instead of the removed sync wrapper
  4. Async worker handles graceful shutdown via SIGTERM/SIGINT without losing in-progress jobs
  5. Lambda/serverless and HTTP trigger modes work in standalone without any Django dependency
**Plans**: TBD
**UI hint**: no

### Phase 3: Testing & CI
**Goal**: Every execution mode has comprehensive test coverage (E2E, edge cases, unit tests) running in CI on both SQLite and PostgreSQL
**Depends on**: Phase 2
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04, TEST-05, TEST-06, TEST-07, TEST-08, TEST-09, TEST-10, TEST-11, TEST-12
**Success Criteria** (what must be TRUE):
  1. CI runs on push to `main` branch (not `master`) and all test jobs pass green
  2. E2E integration tests exist and pass for all 12 mode-integration combinations (6 modes x 2 backends)
  3. Edge case tests cover job timeout, worker crash recovery, retry logic, concurrent workers, zombie detection, and stale heartbeat cleanup
  4. Unit tests exist for core/claiming.py, core/worker.py, core/daemon.py, both backend implementations, and webhooks.py
  5. PostgreSQL-specific test suite runs in CI and covers all execution modes (not just 2 files)
**Plans**: TBD
**UI hint**: no

### Phase 4: Security & Cleanup
**Goal**: Standalone dashboard is secured, attack surfaces (SSRF, CSRF) are mitigated, task module imports are restricted, and dead code is marked for removal
**Depends on**: Phase 3
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-04, CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-04
**Success Criteria** (what must be TRUE):
  1. FastAPI standalone dashboard rejects unauthenticated requests (API key or basic auth required)
  2. Webhook URL validation blocks requests to private IP ranges (10.x, 172.16-31.x, 192.168.x, 169.254.x, localhost)
  3. Django admin API endpoints are protected against CSRF or use token-based auth
  4. Importing task modules is restricted to paths listed in `ALLOWED_TASK_MODULES` when configured
  5. All 24 backward-compatibility stub files and dead async code are annotated with deletion dates (not deleted outright)
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Core Unification | 0/0 | Not started | - |
| 2. Execution Modes & Async Rebuild | 0/0 | Not started | - |
| 3. Testing & CI | 0/0 | Not started | - |
| 4. Security & Cleanup | 0/0 | Not started | - |
