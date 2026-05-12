# Testing Patterns

**Analysis Date:** 2026-05-12

## Test Framework

**Runner:**
- pytest >= 7.4.0
- Config: `pyproject.toml` section `[tool.pytest.ini_options]`

**Key Plugins:**
- `pytest-django` >= 4.5.0 - Django test integration
- `pytest-asyncio` >= 0.23.0 - Async test support
- `pytest-cov` >= 4.1.0 - Coverage reporting
- `pytest-timeout` >= 2.2.0 - Test timeout enforcement
- `hypothesis` >= 6.92.0 - Property-based testing

**Assertion Library:**
- pytest native assertions (plain `assert` statements)
- No third-party assertion library

**Run Commands:**
```bash
uv run pytest tests/ -v                             # Run all tests
uv run pytest tests/ -v --ignore=tests/chaos/ -x    # Run unit tests only (skip chaos), stop on first failure
uv run pytest tests/chaos/ -v --timeout=60           # Run chaos/property tests with timeout
uv run pytest tests/ --ignore=tests/chaos/ --cov=src/sqlery --cov-report=term-missing  # Coverage
```

## Test Configuration

**Django Settings for Tests:**
- File: `tests/settings.py`
- Database: SQLite in-memory (`:memory:`)
- Installed app: `sqlery.django_sqlery`
- Middleware trigger disabled: `ENABLE_MIDDLEWARE_TRIGGER: False`
- Django tasks disabled: `USE_DJANGO_TASKS: False`

**pytest Configuration (`pyproject.toml`):**
```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "tests.settings"
python_files = ["test_*.py"]
testpaths = ["tests"]
```

## Test File Organization

**Location:**
- All tests in separate `tests/` directory (not co-located with source)
- Chaos and property-based tests in `tests/chaos/` subdirectory
- Integration tests directory exists at `tests/integration/` but is empty

**Naming:**
- Test files: `test_<module_or_feature>.py`
- Test classes: `Test<Component><Aspect>` (e.g., `TestQueueProcessing`, `TestFailureHandling`, `TestAtomicJobClaiming`)
- Test methods: `test_<what_is_tested>` (e.g., `test_jobs_processed_in_priority_order`, `test_failed_jobs_captured_with_traceback`)

**Structure:**
```
tests/
  __init__.py
  settings.py                     # Django settings for test environment
  test_admin.py                   # Django admin actions (320 lines)
  test_api.py                     # API endpoint tests (215 lines)
  test_atomic_claiming.py         # SELECT FOR UPDATE locking tests (383 lines)
  test_atomic_scheduler.py        # Atomic scheduler claiming tests (361 lines)
  test_concurrency_and_timeout.py # Concurrency control and timeout (625 lines)
  test_executor.py                # Task executor methods (309 lines)
  test_http_trigger.py            # HTTP trigger middleware (311 lines)
  test_middleware.py              # Scheduled task middleware (152 lines)
  test_models.py                  # Django model tests (452 lines)
  test_queue.py                   # Queue processing tests (434 lines)
  test_scheduler_compat.py        # Scheduler compatibility layer (564 lines)
  test_serialize_worker.py        # Worker serialization (170 lines)
  test_sq55_functools_wraps.py    # functools.wraps behavior tests (372 lines)
  test_subprocess.py              # Subprocess execution (291 lines)
  test_subprocess_middleware.py   # Subprocess middleware (276 lines)
  test_triggers.py                # Trigger module tests (255 lines)
  test_utils.py                   # Utility function tests (51 lines)
  test_version_locking.py         # Optimistic locking tests (244 lines)
  chaos/
    __init__.py
    test_property_based.py        # Hypothesis property-based tests (307 lines)
    test_worker_chaos.py          # Worker crash/kill chaos tests (423 lines)
  integration/
    __init__.py                   # Empty - integration tests not yet implemented
```

**Total test code: ~6,571 lines across 22 test files.**

## Test Structure

**Suite Organization:**
```python
@pytest.mark.django_db
class TestQueueProcessing:
    """Test that queued jobs are processed correctly."""

    def test_jobs_processed_in_priority_order(self):
        """Jobs with higher priority should be processed first."""
        executor = TaskExecutor()

        # Arrange
        job_low = enqueue("tests.test_queue.success_task", priority=1)
        job_high = enqueue("tests.test_queue.success_task", priority=10)
        job_medium = enqueue("tests.test_queue.success_task", priority=5)

        # Act
        processed = executor.run_queue_workers(once=True)

        # Assert
        assert len(processed) == 3
        assert processed[0].id == job_high.id
        assert processed[1].id == job_medium.id
        assert processed[2].id == job_low.id
```

**Patterns:**
- Tests use class-based grouping by feature area with `@pytest.mark.django_db` marker
- Each test class has a descriptive docstring
- Each test method has a one-line docstring describing expected behavior
- Arrange-Act-Assert pattern (implicit, not with comments)
- `TaskExecutor()` is instantiated per-test (no shared fixture)
- No `conftest.py` files; fixtures are minimal

**Setup/Teardown:**
- Django `@pytest.mark.django_db` handles database creation/teardown
- For transaction-level isolation: `@pytest.mark.django_db(transaction=True)`
- Some test classes use `django.test.TestCase` with `setUp()` method for version locking tests:
  ```python
  class TestVersionBasedLocking(TestCase):
      def setUp(self):
          self.worker = Worker.objects.create(node_id="test-node", pid=12345, queues=["default"])
          self.job = QueuedJob.objects.create(task_path="tests.tasks.dummy_task", ...)
  ```

## Test Task Functions

**Pattern:** Define test task functions at module level in each test file:
```python
# tests/test_queue.py

def success_task():
    """A task that succeeds."""
    return "Success"

def failing_task():
    """A task that fails."""
    raise ValueError("Task failed")

def slow_task():
    """A slow task."""
    import time
    time.sleep(0.1)
    return "Done"
```

**Referencing Tasks:** Use full dotted path as strings for `enqueue()`:
```python
job = enqueue("tests.test_queue.success_task")
job = enqueue("tests.test_concurrency_and_timeout.fast_task")
```

**Important:** Task functions MUST be at module level (not inside test classes or methods) to be importable by the executor.

## Mocking

**Framework:** `unittest.mock` (built-in)

**Patterns:**

1. **Patching external triggers:**
```python
from unittest.mock import patch, MagicMock

@patch("sqlery.triggers._enqueue_subprocess")
@patch("sqlery.triggers.get_execution_strategy", return_value="subprocess")
def test_trigger_uses_subprocess(self, mock_strategy, mock_subprocess):
    from sqlery.triggers import trigger_due_tasks
    trigger_due_tasks()
    mock_subprocess.assert_called_once_with()
```

2. **Patching datetime for time-dependent tests:**
```python
from unittest.mock import patch
from datetime import datetime, timezone as dt_tz

frozen_t1 = datetime(2026, 3, 10, 1, 10, 0, tzinfo=dt_tz.utc)
with patch("sqlery.django_sqlery.utils.datetime") as mock_dt:
    mock_dt.now.return_value = frozen_t1
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
    task = ScheduledTask.objects.create(...)
```

3. **Monkeypatching methods (pytest fixture):**
```python
def test_run_queue_workers_processes_one_job_only(self, monkeypatch):
    executor = TaskExecutor()
    spawn_called = []

    def mock_spawn(queue_name=None):
        spawn_called.append(queue_name)

    monkeypatch.setattr(executor, "_spawn_next_worker", mock_spawn)
```

4. **Django request factory (`rf` fixture):**
```python
def test_middleware_calls_response_handler(self, rf):
    request = rf.get("/test/")
    response = MagicMock()
    get_response = MagicMock(return_value=response)
    middleware = ScheduledTaskMiddleware(get_response)
    result = middleware(request)
    get_response.assert_called_once_with(request)
```

5. **Django settings override:**
```python
def test_middleware_triggers_scheduler(self, rf, settings):
    settings.DJANGO_SQL_JOBS = {
        "ENABLE_MIDDLEWARE_TRIGGER": True,
    }
```

**What to Mock:**
- External subprocess spawning (`_spawn_next_worker`)
- Trigger/execution strategy functions
- Django cache (clear before throttle tests)
- Time/datetime for cron schedule tests

**What NOT to Mock:**
- Database operations (use real SQLite in-memory DB)
- Model methods (`mark_running()`, `mark_success()`, `mark_failed()`)
- The `TaskExecutor` class itself (instantiate directly)
- Django ORM queries

## Fixtures and Factories

**Test Data:**
- Jobs and tasks are created inline using Django ORM:
  ```python
  job = QueuedJob.objects.create(
      task_path="tests.test_executor.dummy_task",
      queue_name="default",
      status="queued",
      priority=0,
  )
  ```
- No factory library (no factory_boy or model_bakery)
- No shared fixtures or conftest.py files
- Test data is created directly in each test method

**Enqueue Helper:**
- Use `from sqlery import enqueue, enqueue_at` for higher-level test data creation:
  ```python
  job = enqueue("tests.test_queue.success_task", queue="email", priority=10)
  job_future = enqueue_at("tests.test_queue.success_task", future_time)
  ```

**Location:**
- No separate fixture files
- All test data created inline within test methods or `setUp()`

## Coverage

**Requirements:** No enforced coverage threshold

**CI Coverage Command:**
```bash
uv run pytest tests/ --ignore=tests/chaos/ --cov=src/sqlery --cov-report=term-missing
```

**Coverage Scope:** `src/sqlery` package

## Test Types

**Unit Tests:**
- Scope: Individual model methods, utility functions, executor logic
- Location: `tests/test_models.py`, `tests/test_utils.py`, `tests/test_executor.py`
- Database: SQLite in-memory
- No mocking of internal components; uses real database operations

**Concurrency Tests:**
- Scope: Atomic claiming, version locking, parallel execution control
- Location: `tests/test_atomic_claiming.py`, `tests/test_version_locking.py`
- Uses `@pytest.mark.django_db(transaction=True)` for transaction isolation
- PostgreSQL-specific tests use `@skip_on_sqlite` marker:
  ```python
  skip_on_sqlite = pytest.mark.skipif(
      connection.vendor == "sqlite",
      reason="SQLite does not support SELECT FOR UPDATE SKIP LOCKED"
  )
  ```

**Property-Based Tests (Hypothesis):**
- Scope: Serialization round-trip, edge cases, fuzz testing
- Location: `tests/chaos/test_property_based.py`
- Uses custom Hypothesis strategies for job data:
  ```python
  @st.composite
  def job_arguments(draw):
      args = draw(st.lists(st.one_of(st.none(), st.booleans(), st.integers(), ...), max_size=10))
      kwargs = draw(st.dictionaries(st.text(...), st.one_of(...), max_size=10))
      return args, kwargs
  ```
- Configured with `@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])`

**Chaos Tests:**
- Scope: Worker crashes, SIGKILL recovery, state corruption, resource exhaustion
- Location: `tests/chaos/test_worker_chaos.py`
- Uses `multiprocessing.Process` for subprocess simulation
- Uses `threading.Thread` for concurrent access tests
- Many tests document known failures in module-level docstrings

**Admin Tests:**
- Scope: Django admin actions (enqueue, enable/disable, retry, cancel)
- Location: `tests/test_admin.py`
- Uses `django.test.RequestFactory` via `rf` pytest fixture
- Uses `CookieStorage` for message framework

**Middleware Tests:**
- Scope: Request middleware, throttle behavior, error handling
- Location: `tests/test_middleware.py`
- Uses `time.sleep(0.1)` to wait for background threads

**E2E Tests:**
- Not implemented. The `tests/integration/` directory exists but is empty.

## Common Patterns

**Async Testing:**
- `pytest-asyncio` is listed as a dependency but no async tests exist in the test suite currently
- The `AsyncJobFunction` class has async methods but is not tested with async tests

**Error Testing:**
```python
def test_task_import_error_handled(self):
    """Jobs with invalid task paths should fail gracefully."""
    executor = TaskExecutor()
    job = enqueue("nonexistent.module.task")
    executor.execute_job(job)
    job.refresh_from_db()
    assert job.status == "failed"
    assert "Cannot import task" in job.error or "ImportError" in job.traceback

def test_import_task_not_found():
    """Test importing non-existent task."""
    with pytest.raises(ImportError):
        import_task("nonexistent.module.task")
```

**Database State Verification:**
```python
# Execute, then refresh and verify
executor.execute_job(job)
job.refresh_from_db()
assert job.status == "success"
assert job.started_at is not None
assert job.finished_at is not None
assert job.duration_seconds is not None
```

**Conditional Skipping:**
```python
# Skip tests that require PostgreSQL features
skip_on_sqlite = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="SQLite does not support SELECT FOR UPDATE SKIP LOCKED"
)

# Skip tests that require optional dependencies
@pytest.mark.skipif(not HAS_DJANGO_TASKS, reason="django-tasks not installed")
```

**Timeout Enforcement:**
```python
@pytest.mark.timeout(10)
def test_memory_hog_job(self):
    """Test: Job tries to allocate excessive memory."""
```

## CI Pipeline

**GitHub Actions:** `.github/workflows/test.yml`

**Matrix:**
- Python versions: 3.11, 3.12, 3.13

**Services:**
- PostgreSQL 15 (for PostgreSQL-specific tests)

**Steps:**
1. Install `uv` and Python
2. Install dependencies: `uv pip install -e ".[dev]"`
3. Run unit tests: `pytest tests/ -v --ignore=tests/chaos/ -x`
4. Run chaos/property tests: `pytest tests/chaos/ -v --timeout=60` (allowed to fail)
5. Run PostgreSQL tests: `pytest tests/test_atomic_claiming.py tests/test_atomic_scheduler.py -v --timeout=30` (allowed to fail)
6. Check coverage: `pytest tests/ --ignore=tests/chaos/ --cov=src/sqlery --cov-report=term-missing`

**Notes:**
- Chaos and PostgreSQL tests use `|| echo "..."` so failures do not block CI
- Coverage is reported but no minimum threshold is enforced

## Known Test Limitations

**Documented in test file docstrings:**
- `tests/test_models.py`: Cron schedule recalculation tests may produce same value depending on timing
- `tests/test_queue.py`: `test_next_run_updated_after_enqueue` depends on current wall-clock minute
- `tests/test_atomic_claiming.py`: All `SELECT FOR UPDATE SKIP LOCKED` tests require PostgreSQL
- `tests/test_concurrency_and_timeout.py`: Timeout via `SIGALRM` may not work in all test environments
- `tests/chaos/test_worker_chaos.py`: Uses `claim_job()` method that no longer exists on `TaskExecutor`; local function pickling failures with `multiprocessing`
- `tests/test_triggers.py`: Subprocess spawning fails in test environment (no `manage.py`)
- `tests/test_sq55_functools_wraps.py`: Documents `JobWrapper` pickling limitation as known issue

---

*Testing analysis: 2026-05-12*
