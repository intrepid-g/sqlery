# Testing Patterns

**Analysis Date:** 2026-05-13

## Test Framework

**Runner:**
- `pytest` >= 7.4.0
- Config: `pyproject.toml` `[tool.pytest.ini_options]`
  ```toml
  DJANGO_SETTINGS_MODULE = "tests.settings"
  python_files = ["test_*.py"]
  testpaths = ["tests"]
  ```

**Plugins (declared in `pyproject.toml` `[project.optional-dependencies].dev`):**
- `pytest-django` >= 4.5.0 — Django ORM fixtures, `@pytest.mark.django_db`
- `pytest-asyncio` >= 0.23.0 — `async def` test support
- `pytest-cov` >= 4.1.0 — coverage reporting
- `pytest-timeout` >= 2.2.0 — per-test timeouts (used heavily in chaos suite)
- `hypothesis` >= 6.92.0 — property-based and chaos testing

**Assertion library:** plain `assert` (pytest rewrites assertion failures with diffs).

**Run commands:**
```bash
make test                  # All tests except chaos
make test-coverage         # With coverage report
make test-core             # Models, queue, executor, claiming, scheduler
make test-claiming         # Atomic claiming + version locking
make test-scheduler        # Scheduler + cron
make test-concurrency      # Concurrency + timeout
make test-django           # Admin, middleware, triggers
make test-middleware-mode  # Middleware trigger mode
make test-subprocess-mode  # Subprocess trigger/execution
uv run pytest tests/chaos/ --timeout=60       # Hypothesis chaos suite
uv run pytest tests/test_models.py -v         # Single file
uv run pytest tests/ -k "atomic" -v           # Pattern match
```

## Test File Organization

**Location:** centralized under `tests/` (not co-located with source).

```
tests/
├── __init__.py                  # Empty package marker
├── settings.py                  # Django settings for the test run
├── Dockerfile                   # Postgres-integration container
├── compose.yml                  # docker-compose for full-stack runs
├── test_admin.py
├── test_api.py
├── test_atomic_claiming.py
├── test_atomic_scheduler.py
├── test_concurrency_and_timeout.py
├── test_executor.py
├── test_http_trigger.py
├── test_intervention.py
├── test_job_dependencies.py
├── test_middleware.py
├── test_models.py
├── test_queue.py
├── test_scheduler_compat.py
├── test_serialize_worker.py
├── test_sq55_functools_wraps.py
├── test_subprocess.py
├── test_subprocess_middleware.py
├── test_triggers.py
├── test_ttl_retention.py
├── test_utils.py
├── test_version_locking.py
├── chaos/                       # Hypothesis property-based + chaos suite
│   ├── __init__.py
│   ├── test_property_based.py
│   └── test_worker_chaos.py
└── integration/                 # Integration test placeholder
    └── __init__.py
```

**Naming:** `test_<subject>.py` for files, `Test<Subject>` classes, `test_<behavior>` for methods.

## Django Test Settings

`tests/settings.py` configures:
- `ENGINE = "django.db.backends.sqlite3"`, `NAME = ":memory:"` — fast in-memory default
- `INSTALLED_APPS` includes `"sqlery.django_sqlery"`
- `DJANGO_SQL_JOBS = {"ENABLE_MIDDLEWARE_TRIGGER": False, "USE_DJANGO_TASKS": False}` — sync execution, no auto-trigger

For Postgres runs, `DATABASE_URL=postgresql://...` is honored by the affected test modules (see CI matrix below).

## Test Structure

**Class-based grouping with pytest-django marker:**
```python
@pytest.mark.django_db
class TestScheduledTask:
    """Test ScheduledTask model."""

    def test_scheduled_task_creation(self):
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.tasks.dummy_task",
            cron_expression="0 0 * * *",
        )
        assert task.next_run_at is not None
        assert task.enabled is True
```
See `tests/test_models.py`.

**Patterns observed:**
- One behavior per test method, descriptive name.
- Arrange-Act-Assert with no explicit comment blocks (names carry intent).
- Module-level docstring sometimes documents known-failing tests with root-cause notes (e.g., header of `tests/test_models.py`).
- Direct ORM access (`QueuedJob.objects.create(...)`) for setup — no factories/factory_boy.

## Fixtures and Conftest

No top-level `conftest.py` is checked in — each test module manages its own setup. `pytest-django` provides the global `db` / `transactional_db` fixtures, and the `@pytest.mark.django_db` decorator wraps tests in a per-test transaction with rollback.

**Test data:** inline construction via the Django ORM or SQLModel session. Helpers like `serialize_job_arguments` / `deserialize_job_arguments` (`src/sqlery/utils.py`) are exercised directly with Hypothesis-generated inputs in `tests/chaos/test_property_based.py`.

## Test Categories

**Unit tests (default `tests/*.py`):**
- Model behavior: `test_models.py`, `test_version_locking.py`
- Public API: `test_queue.py`, `test_api.py`, `test_utils.py`
- Execution: `test_executor.py`, `test_serialize_worker.py`
- Claiming algorithm: `test_atomic_claiming.py`
- Scheduling: `test_atomic_scheduler.py`, `test_scheduler_compat.py`

**Django integration:**
- `test_admin.py` — admin views, list/detail pages
- `test_middleware.py`, `test_subprocess_middleware.py` — request-cycle hooks
- `test_triggers.py`, `test_http_trigger.py`, `test_subprocess.py` — trigger/execution modes
- All gated on `@pytest.mark.django_db`

**Concurrency / timing:**
- `test_concurrency_and_timeout.py` — SIGALRM timeouts, fork safety, race windows
- `test_job_dependencies.py`, `test_ttl_retention.py`, `test_intervention.py`

**Chaos / property-based (`tests/chaos/`):**
- `test_property_based.py` — Hypothesis strategies for arbitrary args/kwargs/queue-names, exercising `serialize_job_arguments` / `deserialize_job_arguments` round-trips and queue invariants
  ```python
  @given(args_kwargs=job_arguments())
  @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
  def test_round_trip(self, args_kwargs):
      ...
  ```
- `test_worker_chaos.py` — kill/restart scenarios, zombie detection
- Run with `--timeout=60` in CI; allowed to "complete" even if some examples are skipped (`|| echo "..."` fallback in CI)

**Async:**
- Coroutine-returning code exercised via `pytest-asyncio` (`@pytest.mark.asyncio`)
- Covers `AsyncQueue` (`src/sqlery/async_queue.py`) and `async_worker.py`

## Mocking

**Framework:** `unittest.mock` from the standard library (no `pytest-mock` declared in dev deps).

**Patterns:**
- Patch external SDKs (boto3 EventBridge, httpx) at module scope: `with patch("sqlery.eventbridge_trigger.boto3.client") as m: ...`
- Use real DB (SQLite `:memory:`) rather than mocking the ORM — the claiming/locking logic *is* the contract under test.
- Subprocess tests spawn real `python -m sqlery.core.worker_runner` children to validate fork lifecycle.

**Don't mock:**
- Database ORM (it's the system under test)
- Time-of-day for cron tests where deterministic cron output is the assertion (see notes in `tests/test_models.py` header)

## Coverage

**Tool:** `pytest-cov`

**Local report:**
```bash
make test-coverage
# or:
uv run pytest tests/ --ignore=tests/chaos/ --cov=src/sqlery --cov-report=term-missing
```

**CI:** runs the same coverage command on the last step of every matrix job. No hard threshold is enforced — coverage is reported as `term-missing` for review.

## CI Matrix

Workflow: `.github/workflows/test.yml`

| Axis | Values |
|------|--------|
| Runner | `ubuntu-latest` |
| Python | `3.11`, `3.12`, `3.13` |
| Database (default) | SQLite `:memory:` |
| Database (Postgres step) | `postgres:15` service container, `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/sqlery_test` |
| Trigger | push / pull_request to `master` |

**Steps per matrix entry:**
1. `uv venv && uv pip install -e ".[dev]"`
2. `uv run pytest tests/ -v --ignore=tests/chaos/ -x` — main suite, fail-fast
3. `uv run pytest tests/chaos/ -v --timeout=60` — chaos/property suite (non-blocking via `|| echo`)
4. `uv run pytest tests/test_atomic_claiming.py tests/test_atomic_scheduler.py -v --timeout=30` against Postgres — exercises `SELECT FOR UPDATE SKIP LOCKED` path
5. `uv run pytest tests/ --ignore=tests/chaos/ --cov=src/sqlery --cov-report=term-missing` — coverage report

Note: `pyproject.toml` classifiers also list Python 3.10 and 3.14, but the CI matrix currently exercises 3.11–3.13 only.

## Notable Test Patterns

**Postgres-only tests:** `tests/test_atomic_claiming.py` and `tests/test_atomic_scheduler.py` are written to exercise `SELECT FOR UPDATE SKIP LOCKED`. They run on SQLite in the default lane (where they fall back to the CAS/version path) and again under the Postgres service container.

**Version-locking tests** (`tests/test_version_locking.py`): assert `ConcurrentModificationError` is raised when two updates race with the same `version`.

**Documented-known-failures:** when a test is intentionally flaky or sensitive to clock semantics, the module docstring explains the root cause rather than silently `@pytest.mark.skip`-ing — see header of `tests/test_models.py`.

**Hypothesis settings:** chaos tests use `max_examples=100`, `suppress_health_check=[HealthCheck.function_scoped_fixture]`, and restricted `Phase` configurations to keep CI runtime bounded.

**Docker-based integration (optional):** `tests/Dockerfile` + `tests/compose.yml` allow spinning up a Postgres-backed environment locally; not invoked by CI.

**Sample project:** `sample_project/` (referenced by `Makefile` via `DJANGO_MANAGE := uv run sample_project/manage.py`) is used for manual end-to-end testing of daemon/worker lifecycles, not for unit tests.

**Stress / load:** `stress_test/` directory holds separate scripts driven by Makefile targets (e.g., 120+ job runs) — outside the pytest suite.

---

*Testing analysis: 2026-05-13*
