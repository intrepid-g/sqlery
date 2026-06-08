# Phase 11: Parity-Gated Tests & CI - Pattern Map

**Mapped:** 2026-06-08
**Files analyzed:** 4 (1 new test module, 1-2 extended test modules, 1 CI workflow, 1 marker config)
**Analogs found:** 4 / 4

This phase is **tests + CI only** — no production source changes. Every "file" below is a
test or config file, so role = `test` / `config` and the data flow is the behavioral
assertion shape (matrix-parametrized, cross-backend, lease-driven). The job of the planner
is to write four parity tests + a CI gate by copying the existing precedents catalogued here.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `tests/test_parity_scheduler.py` (NEW — PARITY-01/02/04) | test | matrix-parametrized, lease-driven, event | `tests/unit/test_worker.py::TestWorkerSchedulerElection` + `tests/chaos/test_lease_zombie.py` | exact (role + flow) |
| `tests/test_atomic_scheduler.py` (EXTEND — PARITY-02/03 PG cells) | test | matrix-parametrized, CRON CAS | `TestCronSemanticsHardening` (same file) + `tests/unit/test_sqlalchemy_backend_sync.py::TestLeaseLifecyclePostgres` | exact |
| `tests/chaos/test_lease_zombie.py` (EXTEND — failover PG cell) | test | lease claim/renew/release | `TestLeaseExpiry` / `TestLeaseContentionPostgres` (same file) | exact |
| `.github/workflows/test.yml` (MODIFY — PG rail must run parity) | config | CI matrix gate | "Run @pytest.mark.postgres suite" step (same file) | exact |
| `pyproject.toml` (markers — likely no change) | config | marker registry | `[tool.pytest...] markers` (line 144-146) | exact |

**Per CLAUDE.md GSD rule on edits:** existing wrong lines must be commented out (`# Old:`),
not deleted, with the replacement added beside them. Applies to the CI YAML edits.

## Shared Conventions (apply to all four parity tests)

### 1. The `(integration, db)` parity axis with PG-only marker

The locked decision (11-CONTEXT lines 25-30, 62) is: parametrize each behavioral test over
`{Django, standalone} × {SQLite, Postgres}` and attach `@pytest.mark.postgres` ONLY to the
Postgres `pytest.param`, so the SQLite cell runs on the default rail and the PG cell runs on
the dedicated PG rail. This is the documented pattern in `tests/integration/conftest.py`
(lines 47-59):

```python
# For matrix tests that need both engines, attach the marker to the
# pytest.param('postgres', marks=[pytest.mark.postgres]) row of the
# existing db axis — that way the SQLite cell remains unmarked (runs
# in the default rail) while the Postgres cell only runs in the PG rail.
```

Canonical param shape to copy (adapt from the `db_engine` fixture, conftest lines 579-596):

```python
@pytest.fixture(params=[
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.postgres),
])
def db_engine(request):
    if request.param == "postgres":
        if not os.environ.get("SQLERY_TEST_PG_URL"):
            pytest.skip("postgres engine requires SQLERY_TEST_PG_URL")
        request.node.add_marker(pytest.mark.postgres)
    return request.param
```

For the standalone Django↔standalone axis, two valid approaches exist (pick per file in planning):
- **Integration-harness route** (`tests/integration/conftest.py` `harness` fixture, lines 599-610):
  parametrize `(mode, integration, db)` and let the harness build a `_DjangoHarness` (in-process)
  or `_StandaloneHarness` (no-Django subprocess via `_run_no_django`). Use for E2E PARITY-04.
- **Direct-backend route** (`pg_sync_backend` fixture, sync backend test lines 868-900):
  parametrize `db` only and let `get_backend()` / a per-test engine resolve the active backend.
  Use for lease/CAS-level PARITY-01/02/03 where no subprocess isolation is needed.

### 2. SKIP-not-pass guard when a backend lacks the capability

From `tests/chaos/test_lease_zombie.py` `_lease_supported` (lines 229-245) and the per-test guard:

```python
backend = get_backend()
if not _lease_supported(backend):
    pytest.skip("active backend does not implement queue leases")
```

PG-env guard pattern (sync backend test line 877-879 and lease_zombie line 315-316):

```python
if not os.environ.get("SQLERY_TEST_PG_URL"):
    pytest.skip("SQLERY_TEST_PG_URL not set; PG mirror skipped")
```

Belt-and-suspenders: `tests/integration/conftest.py::pytest_collection_modifyitems`
(lines 115-142) auto-skips ANY item with `"postgres" in item.keywords` when the env var
is unset — so a `@pytest.mark.postgres` test is never an error locally.

### 3. Simulate expiry/failover via a PAST `expires_at`, never a real sleep

From `TestWorkerSchedulerElection` (test_worker.py lines 462, 541-557): never wait a real TTL —
set the prior leader's lease `expires_at` to the past and no-op `time.sleep`:

```python
fake_backend._leases["default"] = {
    "daemon_id": "dead_leader", "node_id": "dead-node", "pid": 111,
    "expires_at": _utcnow() - timedelta(seconds=5),   # already expired -> takeover
}
```

The chaos test's real-TTL variant (`TestLeaseExpiry`, lease_zombie lines 261-276) uses
`lease_secs=1` + `time.sleep(1.5)` and is acceptable only for the genuinely-isolated cell;
prefer PAST-`expires_at` for the inner-loop SQLite cells (11-CONTEXT line 34).

---

## Pattern Assignments

### PARITY-01 — Failover (`test_parity_scheduler.py`, test, lease-driven)

**Analogs:** `TestWorkerSchedulerElection.test_expired_lease_is_taken_over_and_cron_fires`
(test_worker.py 541-559) for the in-process election cell; `TestLeaseExpiry`
(lease_zombie.py 259-276) for the real-backend lease-takeover cell.

**Core pattern to copy** — drive one real election cycle with a spy on lease calls
(`_run_one_election_cycle`, test_worker.py 367-423): patch `time.sleep` to a no-op, wrap
`claim/renew/release_queue_leases`, and make `claim_job` flip `shutdown_requested` so the
worker does exactly one election pass and its `finally:` releases leases. The load-bearing
assertion is that a SECOND `WorkerProcess` claims the queue after the first leader's lease
expires, and a due cron task fires exactly once.

**Parity wrapper:** run this body in all four cells. SQLite cells use FakeBackend / direct
`_leases` manipulation; PG cells use `get_backend()` against `SQLERY_TEST_PG_URL` and the
real `claim_queue_leases` takeover (mirror `TestLeaseExpiry`, marked `@pytest.mark.postgres`).

**TTL fact (REQUIREMENTS ELECT-06):** failover window = `check_interval × 3` (≈30s). Assert
takeover happens within one TTL — simulate by PAST `expires_at`, do not sleep 30s.

---

### PARITY-02 — No-duplicate under two-leader overlap (`test_atomic_scheduler.py` EXTEND, test, CRON CAS)

**Analog:** `TestCronSemanticsHardening.test_cron_fires_exactly_once_under_simulated_overlap`
(atomic_scheduler.py 418-460) and `...under_threaded_overlap` (462-488). These already prove
single-fire on SQLite; Phase 11 must add the PG cells (PG concurrency currently auto-skips).

**Core pattern to copy** (lines 444-456): read `observed_due` once, fire two
`advance_scheduled_task_if_due(task.id, observed_due, new_next_run, job_kwargs)` calls with the
SAME stale `observed_due`, assert exactly one returns non-None and exactly one `QueuedJob` row:

```python
job_a = backend.advance_scheduled_task_if_due(task.id, observed_due, new_next_run, job_kwargs)
job_b = backend.advance_scheduled_task_if_due(task.id, observed_due, new_next_run, job_kwargs)
winners = [j for j in (job_a, job_b) if j is not None]
assert len(winners) == 1
assert QueuedJob.objects.filter(scheduled_task_id=task.id).count() == 1
```

**Scheduler-under-test note (atomic_scheduler.py 378-388):** use
`sqlery.core.scheduler.Scheduler(backend=get_backend())` — NOT the legacy
`sqlery.executor.TaskExecutor`, which still uses SELECT FOR UPDATE SKIP LOCKED and never
calls `advance_scheduled_task_if_due`.

**Parity wrapper:** add a standalone mirror via the `pg_sync_backend` route (sync backend
test 868-900) and a `@pytest.mark.postgres` Django cell — both asserting the same single-fire
invariant. Threaded variant (`run_due_tasks` from 2 threads) covers the real concurrent path.

---

### PARITY-03 — Atomic-advance / drift (`test_atomic_scheduler.py` EXTEND, test, CRON CAS)

**Analog:** `test_next_run_at_advances_without_drift_across_ticks` (atomic_scheduler.py 490+)
and the unit-level drift/clamp tests in `tests/test_scheduler_drift_jitter.py`
(`TestCalculateNextRunDriftClamp` lines 22-82).

**Fixture pattern** — pin an exact, drift-free `next_run_at` via queryset `.update()` to bypass
the model's save-time recalculation (`_make_due_cron_task`, atomic_scheduler.py 395-416):

```python
due = datetime.now(dt_timezone.utc) - timedelta(seconds=past_seconds)
task = ScheduledTask.objects.create(..., next_run_at=due)
ScheduledTask.objects.filter(id=task.id).update(next_run_at=due)  # pin exact value
task.refresh_from_db()
```

**Invariant:** each fired tick sets `next_run_at = calculate_next_run(expr, base_time=prior_next_run_at)`
(future-clamped), NOT from wall-clock `now` (REQUIREMENTS CRON-02). Assert monotonic, drift-free
advance across several ticks. Add PG cells so the Postgres advance path actually runs in CI.

---

### PARITY-04 — Bare-worker E2E (`test_parity_scheduler.py`, test, matrix E2E)

**Analog:** `TestWorkerSchedulerElection.test_bare_worker_fires_due_cron_for_held_queue`
(test_worker.py 465-478) — headline ELECT-04: no daemon object is constructed anywhere;
a `WorkerProcess(queues=["default"], backend=...)` self-elects and fires a due cron.

**Core pattern** (test_worker.py 465-478):

```python
wp = WorkerProcess(queues=["default"], backend=fake_backend)   # NO daemon
task = _seed_due_task(fake_backend, name="bare-cron", queue_name="default")
lease_calls = _run_one_election_cycle(wp, monkeypatch)
assert "default" in _claimed_queues(lease_calls, wp.worker_id)
assert _job_count_for_task(fake_backend, task) == 1            # job is the proof
```

**Real-process E2E option (11-CONTEXT line 35):** for the cells needing true process
isolation, drive a bounded/`--once` worker via the integration harness
(`tests/integration/conftest.py` — `_DjangoHarness._drive_daemon_once` 286-304 invokes
`DaemonManager()._run_daemon(max_workers=1, once=True)`; `_StandaloneHarness` shells out via
`_run_no_django` 483-509). Reuse an existing one-shot worker entry; mark `slow` if it spawns
real subprocesses. Standalone cells MUST run with `DJANGO_SETTINGS_MODULE` scrubbed +
`SQLERY_FORCE_STANDALONE=1` (conftest 487-491).

---

### PARITY-05 — Matrix gate (`.github/workflows/test.yml` MODIFY, config)

**Analog:** the existing PG rail in the same file (lines 76-83):

```yaml
    - name: Run @pytest.mark.postgres suite
      env:
        PYTHONPATH: .
        SQLERY_TEST_PG_URL: postgresql://postgres:postgres@localhost:5432/postgres
        DATABASE_URL: postgresql://postgres:postgres@localhost:5432/sqlery_test
      # Plan 03-07: the exit-code-5 tolerance is removed. PG rail must
      # collect > 0 tests; an empty collection now fails the job.
      run: uv run pytest -m postgres -v --tb=short
```

**What Phase 11 must guarantee (11-CONTEXT 16, 30, 37, 62):** the PG cells of all four
parity tests actually RUN (not skip) and a failing cell fails the build. The existing default
rail already runs `-m "not postgres"` (lines 61, 67, 89) so SQLite parity cells are covered.
The PG rail (line 83) already runs `-m postgres` with `SQLERY_TEST_PG_URL` set and rejects an
empty collection. Steps the planner should verify/extend:
- The new `tests/test_parity_scheduler.py` PG-marked cells are collected by `-m postgres`
  (they will be, since the rail has no path filter).
- Both Django AND standalone PG cells are exercised. The default `test` job runs under
  pytest-django (Django backend). For standalone PG, either ensure the parity test's
  standalone cell runs in-process under `SQLERY_FORCE_STANDALONE` within the PG rail, or add
  an explicit step. Confirm the `standalone-no-django` job (lines 104-159) is import-only and
  does NOT execute parity tests — if standalone PG parity must run, add a dedicated step there
  or in the PG rail with `SQLERY_FORCE_STANDALONE=1`.
- **Edit discipline (CLAUDE.md):** comment out any replaced YAML line with `# Old:` and add
  the new line beside it; do not delete.

---

## Marker / Config Reference

`pyproject.toml` (lines 144-146) already registers both markers — **no change expected**:

```toml
markers = [
    "slow: tests that hit serverful resources (e.g. Postgres); deselect with '-m \"not slow\"'",
    "postgres: requires a running PostgreSQL service (skipped on SQLite-only jobs)",
]
```

`postgres` optional-dep group exists (`pyproject.toml` line 38: `postgres = ["psycopg>=3.1"]`).

## Backend method surface the tests delegate to (production — unchanged this phase)

- `claim_queue_leases(queues, daemon_id, node_id, pid, lease_secs) -> list[str]`
- `renew_queue_leases(owned_queues, daemon_id, lease_secs)`
- `release_queue_leases(owned_queues, daemon_id)`
- `advance_scheduled_task_if_due(task_id, observed_due, new_next_run, job_kwargs) -> job | None`
- `Scheduler(backend=get_backend()).run_due_tasks(queue_names=...)` / `.calculate_next_run(expr, base_time=...)`
- `WorkerProcess(queues=[...], backend=...)` — election lifecycle + `run()` `finally:` lease release

Both `DjangoBackend` and `SQLAlchemyBackend` implement the lease + advance methods as of
Phases 8-10, so a single backend-agnostic test body asserts identical outcomes per cell.

## No Analog Found

None. Every parity test maps onto an existing Phase 8/9/10 precedent; this phase generalizes
those SQLite-proven tests across the full matrix and makes the PG cells run in CI.

## Metadata

**Analog search scope:** `tests/integration/`, `tests/unit/`, `tests/chaos/`, `tests/` root, `.github/workflows/`, `pyproject.toml`
**Files scanned:** 7
**Pattern extraction date:** 2026-06-08
