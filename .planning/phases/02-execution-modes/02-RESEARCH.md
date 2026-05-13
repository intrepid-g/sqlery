# Phase 02: Execution Modes & Async Rebuild — Research

**Researched:** 2026-05-13
**Domain:** async Python (Django 5.2 / SQLAlchemy 2 async), task-queue execution modes, E2E test design
**Confidence:** HIGH on stack and structure; MEDIUM on Django 5.2 async ORM `select_for_update` edge

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **A — Native async ORM** (no `asgiref.sync_to_async`). All async backend methods use `.aget()`, `.acreate()`, `.aupdate()`, async iteration, `transaction.atomic()` async variant.
- **A.1 — Django >= 5.2 LTS minimum.** Drop 4.2 from CI matrix. BC break, documented in release notes.
- **C — Drain-with-deadline shutdown.** New `shutting_down` transient status; `SQLERY_ASYNC_SHUTDOWN_DEADLINE_SECONDS` env (default 60); race resolution via `asyncio.wait({task, deadline_timer}, return_when=FIRST_COMPLETED)`; write-once state.
- **D — Pure-core HTTP trigger.** `src/sqlery/core/triggers.py::handle(envelope)` is framework-agnostic; Django + FastAPI adapters call it.
- **E — Lambda smoke-only.** Import handler, inject fake EventBridge dict, assert job claimed/enqueued. No moto / LocalStack / SAM.
- **F — Single mega-phase, 5 waves.** Do not split 02a/02b.

### Claude's Discretion
- E2E test harness shape — `[ASSUMED]` parametrized pytest matrix `(mode, integration, db)` with `@pytest.mark.slow` on serverful combos. Planner may revisit.
- `SHUTDOWN_DEADLINE_SECONDS` default = 60s — `[ASSUMED]`, researcher recommends keeping.

### Deferred Ideas (OUT OF SCOPE)
- LocalStack / SAM Lambda fidelity testing.
- Async-native scheduler.
- Per-mode harness split (only revisit if matrix becomes painful).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DMOD-01..06 | Django E2E for 6 modes | Existing tests cover middleware/subprocess/triggers (~78 tests), zero coverage of daemon end-to-end or async; parametrized matrix can wrap existing tests |
| SMOD-01..06 | Standalone E2E for 6 modes | Subprocess/HTTP-trigger/Lambda paths must be **built** (none exist on standalone side per PROJECT.md table); daemon/sync are Phase-1-cleared |
| ASYN-01 | Async DatabaseBackend ABC | Mirror existing 30+ method `DatabaseBackend` (`compat/__init__.py:24-655`); 12–15 hot-path methods need async variants (claim, mark_running, mark_success/failed, heartbeat, lease ops, registry add/remove, scheduled-task claim) |
| ASYN-02 | Async DjangoBackend | Native async ORM per decision A; `connection.acursor()` for raw `SELECT FOR UPDATE SKIP LOCKED` on Postgres |
| ASYN-03 | Async SQLAlchemyBackend | `create_async_engine` + `AsyncSession`; `sqlite+aiosqlite://` / `postgresql+asyncpg://` URL families; `with_for_update(skip_locked=True)` works on async session |
| ASYN-04 | AsyncWorker rebuild | Near-greenfield rewrite — `src/sqlery/async_worker.py` references `AsyncStorageBackend = None` (removed in v0.13) |
| ASYN-05 | Graceful shutdown | `loop.add_signal_handler(SIGTERM, ...)` + drain-with-deadline race |

## Summary

5 bullets the planner most needs:

1. **AsyncWorker is greenfield.** `src/sqlery/async_worker.py:18-21` literally has `AsyncStorageBackend = None`. Keep file as a salvage reference (`_load_task`, `_deserialize_args`, `_generate_worker_id` are reusable shapes) but plan as a rewrite, not a refactor.
2. **Async claim path needs raw SQL on Postgres.** Django ORM has `.aget()` / `aupdate_or_create()` but no `.select_for_update()` on the async queryset chain as of 5.2 — drop to `await connection.acursor()` with raw SQL for the `SELECT FOR UPDATE SKIP LOCKED` query, then materialize objects via `.aget(pk=...)`. SQLAlchemy async path has it natively (`stmt.with_for_update(skip_locked=True)` on async session).
3. **Two `shutting_down` migrations required.** Django migration `0026_*` (after `0025_daemoncommand.py`) **and** Alembic revision after `20250101_0013_worker_unique_constraint.py`. Both touch `QueuedJob.status` field — Django field is `max_length=10` (`models.py:385`), `shutting_down` is 13 chars → schema change required (raise `max_length` to ≥15). SQLModel side: `core/models.py:75` is also `max_length=10` and needs the same bump.
4. **Standalone has no daemon/subprocess/trigger/lambda tests today.** `tests/integration/` is an empty package. Existing 18+ tests for subprocess/HTTP-trigger are all Django-coupled (use `pytest-django` fixtures). The parametrized matrix must instantiate the standalone path from scratch — no duplication risk with existing tests.
5. **`lambda_handler.py` is Django-only.** Lines 59-60, 84, 110-120 hard-import `django` and call `setup_django()`. SMOD-04 needs a small `core/lambda_core.py` (mode-agnostic claim+execute helper) and a `fastapi_sqlery/lambda_handler.py` shim. Existing handler is **not** salvageable for standalone — the Django coupling isn't in guards, it's in the control flow.

## Per-Locked-Decision Execution Notes

### A — Native async ORM (Django side)

**Methods available natively in Django 5.2** [CITED: docs.djangoproject.com/en/5.2/topics/async]:
- `Model.objects.aget()`, `.acreate()`, `.aupdate()`, `.aupdate_or_create()`, `.aget_or_create()`, `.afirst()`, `.alast()`, `.acount()`, `.aexists()`, `.adelete()`, `.abulk_create()`, `.abulk_update()`.
- Async iteration: `async for obj in Model.objects.filter(...):`.
- Async transactions: `from asgiref.sync import sync_to_async` was the 4.2 pattern; 5.0+ ships `transaction.aatomic()` — **wait**: as of 5.2 the async transaction API is still raw `await sync_to_async(transaction.atomic)(...)` for context manager use OR direct `await connection.aensure_connection()` patterns. [ASSUMED — verify in implementation].
- **NOT available natively:** `.select_for_update(skip_locked=True)` on async querysets. Pattern: open `async with connection.acursor() as cur:` → execute raw SQL → fetch IDs → `await QueuedJob.objects.aget(pk=id)`.

**Implication for ASYN-02:** `aclaim_job()` is the only method that needs raw SQL. All other async methods (mark_running, mark_success/failed, heartbeat, lease claim/renew/release, registry ops, scheduled-task claim) map cleanly to native async ORM.

### A.1 — Django 5.2

`pyproject.toml` currently pins `django>=4.2` in 4 places (lines 45, 66, 85, 110). All four bump to `django>=5.2`. CI matrix: drop 4.2, keep 5.2 + (optionally) 5.1 as a smoke run. **Verify before pinning:** `npm view`-equivalent for PyPI: `pip index versions django` or check pypi.org/project/django/ — Django 5.2 LTS released **April 2025** [CITED: djangoproject.com/download/]. Python 3.10+ is supported by 5.2.

### C — `shutting_down` status migration

**Schema delta:**
- `src/sqlery/django_sqlery/models.py:384-386` — `status = CharField(max_length=10, ...)`. `shutting_down` = 13 chars. **Raise `max_length` to 20** (gives slack for future states).
- `src/sqlery/core/models.py:75` — `status: str = Field(default="queued", max_length=10, ...)`. Same bump.
- Both `STATUS_CHOICES` tuples (`django_sqlery/models.py:350-356`, `core/models.py` enum/literal if present) add `("shutting_down", "Shutting Down")`.

**Migration files:**
- Django: new `src/sqlery/django_sqlery/migrations/0026_shutting_down_status.py` (current head is `0025_daemoncommand.py`). Use `AlterField` to bump `max_length` and update choices.
- Alembic: new revision after `20250101_0013_worker_unique_constraint.py`. Use `op.alter_column('queued_job', 'status', type_=sa.String(20))`.

**Race resolution code shape (ASYN-05):**
```python
deadline = asyncio.create_task(asyncio.sleep(deadline_secs))
done, pending = await asyncio.wait(
    {job_task, deadline}, return_when=asyncio.FIRST_COMPLETED
)
if job_task in done:
    # Won — mark finished/failed normally
else:
    job_task.cancel()
    await backend.amark_failed(job_id, error="shutdown_timeout: ...")
```

State is **write-once** — once `shutting_down` flips to `finished`/`failed`, the transition wins. No re-flip back to `running`.

### D — Pure-core HTTP trigger split

**Current state of `src/sqlery/triggers.py`** (147 lines, top-level stub): three execution strategies (subprocess / django-tasks / thread) for `trigger_due_tasks()` and `trigger_queue_workers()`. **It's not an HTTP trigger receiver** — it's a strategy dispatcher.

**The actual HTTP receiver** lives in `src/sqlery/django_sqlery/http_trigger_middleware.py` (HTTP **sender** middleware) plus a `views.py` endpoint that receives the signed POST. The signature verification helper is `src/sqlery/django_sqlery/signature.py::make_signed_request_headers`.

**Refactor for D:**
- New `src/sqlery/core/triggers.py` — pure function `handle(envelope: TriggerEnvelope) -> TriggerResult` containing: signature verification (move `signature.py` to `core/signature.py` first or import from there), idempotency cache check (abstract over `cache` provider), strategy dispatch to subprocess/thread/django-tasks.
- Django adapter: `src/sqlery/django_sqlery/views.py::trigger_view` parses Django `request` → builds envelope → calls `core.triggers.handle()` → returns `JsonResponse`.
- FastAPI adapter: new `src/sqlery/fastapi_sqlery/triggers.py` mounting `POST /trigger` on existing `sqlery-web` app (`fastapi_sqlery/app.py`).

**Existing top-level `src/sqlery/triggers.py`** (the 147-line strategy file) stays as a dated stub re-exporting from `django_sqlery/triggers.py` (which itself becomes the Django-side strategy module). Aligns with Phase 1's stub-don't-delete dead-code policy.

### E — Lambda smoke-only

**DMOD-04:** `tests/integration/test_lambda_django.py` — import `sqlery.lambda_handler.handler`, build `event = {"action": "process_queue", "queue_name": "default"}`, call `handler(event, fake_context)`, assert via the Django ORM that one queued job moved to `running` (or `success`).

**SMOD-04:** requires building `src/sqlery/fastapi_sqlery/lambda_handler.py` first (new file, no Django imports). Shape: `handler(event, context)` → `from sqlery.compat import initialize; initialize(database_url=os.environ["SQLERY_DATABASE_URL"]); ...`. Then a parallel smoke test.

**No new deps.** No `moto`, no `localstack`, no `aws-sam-cli`. The smoke test is a synchronous in-process call.

### F — Wave breakdown refinement

CONTEXT.md outlined 5 waves; recommend refining as follows (concrete plan stubs):

| Wave | Plans | Parallelism | Depends on |
|------|-------|-------------|------------|
| **W1 — Foundations** | (a) `pyproject` Django 5.2 bump + CI matrix drop 4.2 + 5.2 row; (b) `shutting_down` status migrations (Django 0026 + Alembic + model field bumps); (c) ASYN-01 async ABC in `compat/__init__.py` (parallel `AsyncDatabaseBackend` class) | a/b/c parallel | Phase 1 complete |
| **W2 — Async backends** | (d) ASYN-02 Django async backend `django_sqlery/async_backend.py`; (e) ASYN-03 SQLAlchemy async backend `fastapi_sqlery/async_backend.py` + async engine in `database.py` | d/e parallel | W1.c |
| **W3 — AsyncWorker + shutdown** | (f) AsyncWorker rewrite in `core/async_worker.py` (move out of top-level `sqlery/async_worker.py`); (g) drain-with-deadline shutdown semantics + `SHUTDOWN_DEADLINE_SECONDS` config | g folds into f | W2.d, W2.e |
| **W4 — Existing-mode E2E matrix** | (h) Pytest parametrized harness `tests/integration/test_modes.py` + fixtures; (i) DMOD-01/02/03/05 wiring (existing Django modes); (j) SMOD-01, SMOD-05 (daemon, sync on standalone) | i/j parallel after h | W1 done (no async dep) |
| **W5 — Net-new modes** | (k) SMOD-02 subprocess standalone (new `fastapi_sqlery/subprocess_executor.py`); (l) SMOD-03 + D refactor (core/triggers.py + adapters); (m) DMOD-04 / SMOD-04 Lambda smoke (+ new `fastapi_sqlery/lambda_handler.py`); (n) DMOD-06 / SMOD-06 AsyncWorker E2E | k/l/m parallel; n depends on W3 | W3, W4 harness |

## Per-Research-Question Findings

### 1. Django 5.2 async ORM coverage for the claim path

**Verified** [VERIFIED: file inspection]: `compat/__init__.py:65-73` defines `claim_job(queues, worker_id)`; current Django impl (`django_sqlery/backend.py:101-141`) uses `atomic_claim_job_queryset` from `db_compat.py` which wraps `select_for_update(skip_locked=True)`.

**Native async coverage** [CITED: docs.djangoproject.com/en/5.2/topics/async/, docs.djangoproject.com/en/5.2/topics/db/queries/#async-queries]:
- All single-object operations have `a`-prefixed variants.
- `QuerySet.select_for_update()` is documented as **synchronous-only** — async iteration of a `select_for_update`-wrapped queryset will raise `SynchronousOnlyOperation` [ASSUMED — needs implementation-time confirmation].
- Recommended async pattern for atomic claim: raw SQL via `connection.acursor()`:
  ```python
  async with connection.acursor() as cur:
      await cur.execute(
          "SELECT id FROM sqlery_queued_job "
          "WHERE queue_name = ANY(%s) AND status = 'queued' "
          "  AND (scheduled_at IS NULL OR scheduled_at <= NOW()) "
          "ORDER BY priority DESC, created_at "
          "FOR UPDATE SKIP LOCKED LIMIT 1",
          [queues],
      )
      row = await cur.fetchone()
  ```
  Then `await QueuedJob.objects.aget(pk=row[0])` for the ORM object, and `await job.aupdate(status='running', ...)`.

**SQLite optimistic-lock path:** the existing `version`-field CAS pattern works async via `await QueuedJob.objects.filter(pk=id, version=v).aupdate(status='running', version=v+1)` returning rowcount. No raw SQL needed.

### 2. asyncpg vs psycopg3-async

**Recommendation: psycopg3 (`psycopg`) async.** Reasoning:
- `psycopg>=3.1` is **already a dep** (`pyproject.toml:38, 68, 73, 87`). No new dep per the "No new dependencies" constraint in CLAUDE.md.
- psycopg3 has first-class async support via `psycopg.AsyncConnection` and works as a SQLAlchemy 2.x async driver via URL `postgresql+psycopg://...` [CITED: docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg].
- asyncpg is faster but: (a) new dep, (b) doesn't speak the DBAPI exactly the way psycopg3 does, (c) has known quirks with SQLAlchemy ORM type adapters for JSON/array.

**Standalone async engine URL:** `postgresql+psycopg://user:pw@host/db` (this is the **same** URL family as the sync path with the `+psycopg` driver tag — SQLAlchemy auto-detects async use via `create_async_engine`).

### 3. aiosqlite + SQLAlchemy async engine

**Verified pattern** [CITED: docs.sqlalchemy.org/en/20/dialects/sqlite.html#aiosqlite]:
```python
from sqlalchemy.ext.asyncio import create_async_engine
engine = create_async_engine("sqlite+aiosqlite:///path.db",
                              connect_args={"check_same_thread": False})
```

**aiosqlite is a new dep.** PyPI package, MIT, stable. Add to `[project.optional-dependencies]` standalone extra. ~30KB pure-Python wrapper over `sqlite3` running on a thread executor. Acceptable per "prefer existing deps" but unavoidable for SMOD-06.

**WAL / busy_timeout quirk:** the existing sync path enables WAL in `django_sqlery/apps.py` via Django's `connection_created` signal — that doesn't fire on the SQLAlchemy path. Standalone WAL is currently handled in `fastapi_sqlery/database.py` (`StaticPool` + `check_same_thread=False`) but does NOT enable WAL pragma. **Add to async engine init:**
```python
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()
```
For aiosqlite, the same listener works on `sync_engine`. Flag for planner: this is a latent gap in the *sync* standalone path too — Phase 1 may have left this open. **Out of scope for Phase 02 unless cheap to fold in.**

### 4. Existing AsyncWorker state

`src/sqlery/async_worker.py` — 283 lines. **Broken since v0.13** (PROJECT.md line 77).

| Component | Verdict |
|-----------|---------|
| `class AsyncWorker.__init__` (lines 33-56) | Salvage shape; rewrite signature to accept `AsyncDatabaseBackend` |
| `async def work()` (58-85) | Rewrite — must integrate drain-with-deadline |
| `_claim_job()` (92-99) | Rewrite — delegates to broken `AsyncStorageBackend` |
| `_process_job()` (101-137) | Salvage — sync-in-executor fallback is correct |
| `_load_task()` (139-176) | **Reuse as-is** — pure utility, unchanged |
| `_deserialize_args()` (178-192) | **Reuse as-is** |
| `_setup_signal_handlers()` (210-218) | **Discard** — uses `signal.signal()` which races with asyncio loop. Replace with `loop.add_signal_handler(SIGTERM, self._initiate_shutdown)`. |
| `_generate_worker_id()` (220-232) | Reuse; consider switching to uuid7 like sync path |

**File location:** move to `src/sqlery/core/async_worker.py` (matches `core/worker.py` sibling) and leave the top-level `src/sqlery/async_worker.py` as a dated stub re-exporting (Phase 1 stub policy).

### 5. Existing test inventory (no-duplication scan)

Existing test files relevant to phase 02 modes:

| File | Tests | Mode coverage | Integration |
|------|-------|---------------|-------------|
| `tests/test_subprocess.py` | 21 | Subprocess execution mechanics | Django |
| `tests/test_subprocess_middleware.py` | 16 | Subprocess via middleware | Django |
| `tests/test_http_trigger.py` | 17 | HTTP trigger mode | Django |
| `tests/test_triggers.py` | 18 | Trigger strategy dispatch (the 147-line `triggers.py`) | Django |
| `tests/test_middleware.py` | 7 | Daemon middleware (request-cycle worker trigger) | Django |
| `tests/test_atomic_claiming.py` | — | Claim primitive | Both (post-Phase-1) |
| `tests/test_core_standalone.py` | 2 | Phase-1 standalone smoke | Standalone |
| `tests/chaos/test_worker_chaos.py` | — | Crash/zombie | Django |

**Gaps the parametrized E2E matrix must fill:**
- **No** daemon E2E test exists for Django (DMOD-01 is net-new).
- **No** Lambda test exists for either side (DMOD-04, SMOD-04 net-new).
- **No** sync/thread E2E test exists (DMOD-05, SMOD-05 net-new — exists only as side-effect of `test_triggers.py` strategy=thread).
- **No** async worker test exists anywhere (DMOD-06, SMOD-06 net-new).
- **No** standalone test exists for ANY mode except the Phase-1 import smoke (SMOD-01..06 all net-new on the standalone axis).

**Recommendation:** keep existing 80+ tests as `unit/mechanic` coverage. Phase 02 ships a separate `tests/integration/test_modes.py` with the `(mode, integration, db)` parametrization. Zero duplication.

### 6. HTTP trigger split (see §D above for full plan)

Key file moves:
- `src/sqlery/django_sqlery/signature.py` → `src/sqlery/core/signature.py` (pure HMAC, no Django) + dated stub at old path.
- New `src/sqlery/core/triggers.py` with `handle(envelope: dict) -> dict` — the existing 147-line top-level `triggers.py` is **not** the HTTP receiver and remains a strategy stub.
- Django adapter: extend `src/sqlery/django_sqlery/views.py` (currently exists per file listing).
- FastAPI adapter: new `src/sqlery/fastapi_sqlery/triggers.py` mounting `POST /trigger` on the existing app.

### 7. Migration heads

- **Django head:** `src/sqlery/django_sqlery/migrations/0025_daemoncommand.py`. New: `0026_add_shutting_down_status.py`.
- **Alembic head:** `alembic/versions/20250101_0013_worker_unique_constraint.py`. New: `20260514_0014_add_shutting_down_status.py` (use today's date prefix to keep the existing date-ordered convention).
- `alembic.ini` lives at repo root (not under `src/sqlery/fastapi_sqlery/`); the script_location may need verification by the planner.

### 8. Fork-safety in the async path

**Confirmed: AsyncWorker does NOT fork.** It's a single-process asyncio event loop that runs jobs as `asyncio.Task` (or for sync funcs, `loop.run_in_executor(None, ...)` per `async_worker.py:124-125`). No `os.fork()`. Phase 1 fork-safety rules (close DB connections around fork, `os.setpgrp()` in child) **do not apply** to the async path.

Caveat: if a user marks an async-defined job as CPU-bound and runs it through `run_in_executor`, the default executor is a ThreadPoolExecutor — threads share fds with the parent, no fork-safety needed. **Don't fork in async.**

### 9. Pitfalls — flagged for planner

- **Don't use `signal.signal()` in async code.** It races with the event loop. Use `asyncio.get_running_loop().add_signal_handler(SIGTERM, callback)`. The existing `async_worker.py:210-218` does this wrong; the rewrite must not copy it.
- **Don't import Django at module load in `core/async_worker.py`.** Use the lazy `get_backend()` from compat. The mode-detection logic in `compat/__init__.py:677-693` returns 'django' only if `django.conf.settings.configured` is True, so a misconfigured import order can silently route to standalone.
- **psycopg3 async cursors are single-use.** Don't share `AsyncCursor` across awaits without `async with`. Also: `async with connection.acursor()` is the Django wrapper, not raw psycopg.
- **SQLAlchemy async sessions:** `AsyncSession` requires `session.execute()` not `session.exec()` for async — `SQLModel.exec()` is sync-only. Use raw `await session.execute(stmt)` + `.scalars().first()`.
- **`STATUS_CHOICES` is duplicated 3x** in `django_sqlery/models.py` (lines 350, 1024, 1130) for different models. Only line 350 (QueuedJob) needs the `shutting_down` addition.
- **Lambda handler runs `django.setup()` at import time** if it sees `DJANGO_SETTINGS_MODULE`. The standalone Lambda shim must NOT call `setup_django()`. Build it as a parallel file, not a guarded edit of the existing one.
- **`scheduled_at` async filter pitfall:** Django's `Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())` works in async, but `timezone.now()` is sync. Capture the timestamp before the `await`: `now = timezone.now(); await ...filter(scheduled_at__lte=now)`.
- **The 21 legacy `sqlery.executor` callers** flagged in 01-VERIFICATION.md (`management/commands/run_jobs.py` etc.) are NOT in scope for Phase 02 per CONTEXT.md gotcha #1 — leave for Phase 4. Don't touch unless directly in a mode you're rewiring.
- **`tests/integration/` is an empty package** (only `__init__.py`). Plan to populate it with the new parametrized matrix, not extend `tests/` flat.
- **`requests` is referenced by `webhooks.py` but not in `pyproject.toml`** (per CLAUDE.md). Not in scope here, but if any new code touches webhooks, surface the gap.
- **Django 5.2 minimum Python is 3.10** — already our floor. Verify `pyproject.toml` `requires-python` line is `>=3.10` (it is) and don't accidentally raise it.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Django 5.2 async queryset does NOT support `.select_for_update(skip_locked=True)` | §1, Pitfalls | Med — falls back to raw `acursor()` SQL, which is the planned path anyway |
| A2 | `transaction.aatomic()` async context manager works in 5.2 LTS | §A | Low — `sync_to_async(transaction.atomic)` is the proven fallback |
| A3 | `SHUTDOWN_DEADLINE_SECONDS` default of 60s is reasonable for the user's workload | §C | Low — env var override available |
| A4 | E2E test harness shape (parametrized `(mode, integration, db)` matrix) is acceptable | CONTEXT.md decision B | Med — surface in PLAN.md so user can override |
| A5 | Alembic revision date-prefix convention should continue with today's date | §7 | Low — purely naming |

## Open Questions for the Planner

1. **Should Django 5.1 stay in the CI matrix as a smoke row, or hard-cut to 5.2 only?** CONTEXT.md says "CI matrix drops 4.2" — silent on 5.1. Recommend 5.2 only for simplicity.
2. **Is the WAL pragma gap in the sync standalone path (§3) in scope to fix incidentally?** Cheap fix, but technically a Phase 4 hardening item.
3. **Top-level `sqlery/triggers.py` strategy file** — keep as live module or convert to dated stub? Currently has 18 tests against it (`test_triggers.py`). Recommend: leave live, add new `core/triggers.py` for the HTTP receiver as a distinct surface.

## Sources

### Primary (HIGH confidence)
- `src/sqlery/compat/__init__.py:24-655` — DatabaseBackend ABC (30+ methods)
- `src/sqlery/django_sqlery/backend.py:101-141` — current sync `claim_job`
- `src/sqlery/fastapi_sqlery/backend.py:109-141` — current SQLAlchemy `with_for_update(skip_locked=True)`
- `src/sqlery/async_worker.py` (full 283 lines) — broken state inventory
- `src/sqlery/lambda_handler.py:59-120` — Django coupling locations
- `src/sqlery/django_sqlery/models.py:350-386` — STATUS_CHOICES and max_length
- `src/sqlery/core/models.py:75` — SQLModel status max_length
- `pyproject.toml:44-110` — Django pin locations (4 entries)
- `alembic.ini`, `alembic/versions/20250101_0013_*` — migration head
- `src/sqlery/django_sqlery/migrations/0025_daemoncommand.py` — Django migration head
- `tests/` directory inventory — test coverage gap analysis

### Secondary (MEDIUM confidence — official docs)
- Django 5.2 async ORM docs (cited inline)
- SQLAlchemy 2.x async + asyncio / aiosqlite / psycopg dialect docs (cited inline)

### Tertiary (LOW confidence — needs implementation-time verification)
- Exact form of `transaction.aatomic()` in Django 5.2 — flagged as A2
- Behavior of `select_for_update` on async queryset — flagged as A1

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified against `pyproject.toml` and existing backend code
- Architecture: HIGH — followed Phase 1's compat / core / integration tier model
- Pitfalls: HIGH — most pitfalls observed directly in current code (signal handler bug, max_length, lambda coupling)
- Async-ORM specifics: MEDIUM — two items flagged as `[ASSUMED]` need implementation confirmation

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (30 days; Django 5.2 is LTS — stable)
