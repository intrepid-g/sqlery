# Requirements: Sqlery Feature-Complete Run Modes

**Defined:** 2026-05-12
**Core Value:** Every execution mode works reliably and is tested in CI across both Django and standalone integration modes, on both SQLite and PostgreSQL.

## v1 Requirements

### Code Unification

- [ ] **UNIF-01**: Claiming algorithm consolidated — `django_sqlery/worker_claiming.py` delegates to `core/claiming.py` via backend abstraction
- [ ] **UNIF-02**: Execution logic consolidated — `django_sqlery/executor.py` delegates to `core/worker.py` JobExecutor via DjangoBackend
- [ ] **UNIF-03**: Django imports removed from `core/` package — all 8 affected modules route through compat layer or try/except guards
- [ ] **UNIF-04**: `core/worker.py` works without Django installed (standalone mode)
- [ ] **UNIF-05**: `core/daemon.py` works without Django installed (standalone mode)
- [ ] **UNIF-06**: `core/db_resilience.py` works without Django installed (standalone mode)

### Execution Modes — Django

- [ ] **DMOD-01**: Daemon mode passes E2E test (enqueue → claim → execute → complete) in Django
- [ ] **DMOD-02**: Subprocess mode passes E2E test in Django
- [ ] **DMOD-03**: HTTP trigger mode passes E2E test in Django
- [ ] **DMOD-04**: Lambda/serverless mode passes E2E test in Django (mocked Lambda invocation)
- [ ] **DMOD-05**: Synchronous/thread mode passes E2E test in Django
- [ ] **DMOD-06**: Async worker mode passes E2E test in Django

### Execution Modes — Standalone

- [ ] **SMOD-01**: Daemon mode passes E2E test in standalone (FastAPI/SQLAlchemy)
- [ ] **SMOD-02**: Subprocess mode implemented and passes E2E test in standalone
- [ ] **SMOD-03**: HTTP trigger mode implemented and passes E2E test in standalone
- [ ] **SMOD-04**: Lambda/serverless mode implemented and passes E2E test in standalone
- [ ] **SMOD-05**: Synchronous/thread mode passes E2E test in standalone
- [ ] **SMOD-06**: Async worker mode implemented with real async backend (asyncpg/aiosqlite) and passes E2E test

### Async Worker Rebuild

- [ ] **ASYN-01**: Async DatabaseBackend ABC defined in compat layer with all required methods
- [ ] **ASYN-02**: Async Django backend implementation (wrapping sync ORM or using async ORM)
- [ ] **ASYN-03**: Async SQLAlchemy backend implementation (using asyncpg/aiosqlite)
- [ ] **ASYN-04**: AsyncWorker class refactored to use new async backend
- [ ] **ASYN-05**: Async worker supports graceful shutdown via signal handling

### Testing

- [ ] **TEST-01**: E2E integration tests for each execution mode in Django (6 modes × enqueue/claim/execute/complete)
- [ ] **TEST-02**: E2E integration tests for each execution mode in standalone (6 modes)
- [ ] **TEST-03**: Edge case tests: job timeout, worker crash recovery, retry logic, concurrent workers
- [ ] **TEST-04**: Edge case tests: zombie job detection, stale heartbeat cleanup, queue lease expiry
- [ ] **TEST-05**: Unit tests for core/claiming.py (tag concurrency, rate limits, dependencies, TTL expiry)
- [ ] **TEST-06**: Unit tests for core/worker.py (fork lifecycle, signal handling, connection reset)
- [ ] **TEST-07**: Unit tests for core/daemon.py (daemon lifecycle, scheduler integration, worker pool management)
- [ ] **TEST-08**: Unit tests for fastapi_sqlery/backend.py (SQLAlchemyBackend — all 30+ DatabaseBackend methods)
- [ ] **TEST-09**: Unit tests for django_sqlery/backend.py (DjangoBackend — all 30+ DatabaseBackend methods)
- [ ] **TEST-10**: Unit tests for webhooks.py (HMAC signing, retry logic, HTTP delivery)
- [ ] **TEST-11**: PostgreSQL-specific tests in CI for all modes (not just 2 files)
- [ ] **TEST-12**: CI workflow triggers fixed (master → main branch)

### Security

- [ ] **SEC-01**: FastAPI standalone dashboard has authentication (API key or basic auth middleware)
- [ ] **SEC-02**: Webhook URLs validated against SSRF — private/link-local IP ranges blocked
- [ ] **SEC-03**: Django admin API endpoints have CSRF protection or token-based auth
- [ ] **SEC-04**: `ALLOWED_TASK_MODULES` config option restricts importable task paths

### Cleanup

- [ ] **CLEAN-01**: 24 backward-compatibility stub files marked for removal (commented with deletion date)
- [ ] **CLEAN-02**: Dead AsyncStorageBackend = None code marked for removal (after async rebuild replaces it)
- [ ] **CLEAN-03**: Commented-out code blocks throughout codebase marked with deletion dates
- [ ] **CLEAN-04**: Webhook import bug fixed (`django_sqlery.webhooks` → `sqlery.webhooks`)

## v2 Requirements

### Performance

- **PERF-01**: PostgreSQL LISTEN/NOTIFY for instant job wakeup (replaces polling)
- **PERF-02**: TTL expiry moved from claiming hot path to periodic daemon cleanup
- **PERF-03**: Job result size limits to prevent database bloat

### Operational

- **OPS-01**: API endpoint rate limiting (FastAPI and Django)
- **OPS-02**: Job result size limits configuration
- **OPS-03**: Connection pooling for standalone mode forked workers (NullPool or re-init after fork)

### Documentation

- **DOC-01**: Execution modes documented with configuration examples
- **DOC-02**: Migration guide for each mode (from RQ, Celery, django-tasks-scheduler)
- **DOC-03**: Security model documentation

## Out of Scope

| Feature | Reason |
|---------|--------|
| Redis/RabbitMQ backends | Sqlery is database-backed by design |
| Mobile/desktop clients | This is a Python library |
| Dashboard UI redesign | Existing dashboards get auth, not a rewrite |
| Multi-tenancy | Library for single-project use |
| New trigger modes beyond 6 | Future milestone |
| Immediate deletion of dead code | User prefers comment-and-date-mark approach |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| UNIF-01 | — | Pending |
| UNIF-02 | — | Pending |
| UNIF-03 | — | Pending |
| UNIF-04 | — | Pending |
| UNIF-05 | — | Pending |
| UNIF-06 | — | Pending |
| DMOD-01 | — | Pending |
| DMOD-02 | — | Pending |
| DMOD-03 | — | Pending |
| DMOD-04 | — | Pending |
| DMOD-05 | — | Pending |
| DMOD-06 | — | Pending |
| SMOD-01 | — | Pending |
| SMOD-02 | — | Pending |
| SMOD-03 | — | Pending |
| SMOD-04 | — | Pending |
| SMOD-05 | — | Pending |
| SMOD-06 | — | Pending |
| ASYN-01 | — | Pending |
| ASYN-02 | — | Pending |
| ASYN-03 | — | Pending |
| ASYN-04 | — | Pending |
| ASYN-05 | — | Pending |
| TEST-01 | — | Pending |
| TEST-02 | — | Pending |
| TEST-03 | — | Pending |
| TEST-04 | — | Pending |
| TEST-05 | — | Pending |
| TEST-06 | — | Pending |
| TEST-07 | — | Pending |
| TEST-08 | — | Pending |
| TEST-09 | — | Pending |
| TEST-10 | — | Pending |
| TEST-11 | — | Pending |
| TEST-12 | — | Pending |
| SEC-01 | — | Pending |
| SEC-02 | — | Pending |
| SEC-03 | — | Pending |
| SEC-04 | — | Pending |
| CLEAN-01 | — | Pending |
| CLEAN-02 | — | Pending |
| CLEAN-03 | — | Pending |
| CLEAN-04 | — | Pending |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 0
- Unmapped: 40 ⚠️

---
*Requirements defined: 2026-05-12*
*Last updated: 2026-05-12 after initial definition*
