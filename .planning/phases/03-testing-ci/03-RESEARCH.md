# Phase 03: Testing & CI — Research

**Researched:** 2026-05-14
**Domain:** pytest / pytest-django / pytest-cov / GitHub Actions / Hypothesis / Django migration audit
**Confidence:** HIGH (codebase-grounded; all claims verified against repo state)

## Summary

- **D-02-07-1 is NOT a duplicate `CreateModel`.** The repo already attempts a fix in `0023_restore_daemonlease.py` (now a conditional `RunPython`). Root cause is actually `0022_delete_daemonlease_alter_jobregistry_metadata_and_more.py`: filename advertises a `DeleteModel("DaemonLease")` but the `operations = [...]` list (lines 13-73) contains **only `AlterField` calls — the `DeleteModel` operation is missing**. Django's migration graph state therefore says the model is gone (filename + autodetector implied a delete had it been generated), but the table from `0020_daemon_lease.py:12` is still present at runtime. Then `0023` (in its **original unconditional form**, now commented out at lines 38-53) ran `CreateModel('DaemonLease')` and crashed on table-already-exists. The conditional `RunPython` in current `0023` papers over the crash for `migrate`, but pytest-django's `setup_databases` flushes/recreates differently — see Pitfalls below.
- **TEST-12 (master → main) is NOT fixed.** `.github/workflows/test.yml:5-7` still triggers on `master` for both `push` and `pull_request`. One-line fix.
- **Unit-test coverage of the six target modules is near zero.** Of `core/claiming.py`, `core/worker.py`, `core/daemon.py`, `fastapi_sqlery/backend.py`, `django_sqlery/backend.py`, `webhooks.py` — only `webhooks.py` has zero matching test files; the others are only referenced via integration-style tests (`test_core_standalone.py`, `test_executor.py`, `test_ttl_retention.py`), not focused unit tests. SQLAlchemyBackend (sync) has **no** unit tests; the async variant does (`test_sqlalchemy_async_backend.py`).
- **`tests/chaos/` is already broken** (header comment in `test_worker_chaos.py:1-20` self-documents API drift from the Phase 1 refactor: `TaskExecutor.claim_job()` no longer exists; pickling of locally-defined task functions fails; `Worker` import ambiguity). Plan 03-04/03-05 must triage existing tests before extending.
- **No `pytest.mark.postgres` marker exists today.** Only `slow` is registered (`pyproject.toml:144`). Adding `postgres` is collision-free.

**Primary recommendation:** Land D-02-07-1 fix and TEST-12 fix as Wave 1 (foundation). Wave 2 is unit tests across the 6 target modules (parallelizable). Wave 3 is chaos/edge-case rebuild + coverage gate. Keep the chaos triage in its own plan — do not try to extend a broken harness.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary | Rationale |
|---|---|---|---|
| Django migration fix (D-02-07-1) | Django ORM / migrations | pytest-django setup_databases | Migration graph must round-trip cleanly through `makemigrations --check` and `setup_databases` |
| Unit tests (claiming/worker/daemon) | Core (framework-agnostic) | both backends as fakes | Per CLAUDE.md, core must work without Django |
| Backend unit tests | Django ORM + SQLModel/SQLAlchemy | — | Each backend has 30+ methods to cover |
| Chaos / concurrency tests | OS process (subprocess.Popen) + DB | Hypothesis | Real fork semantics, not threads |
| CI matrix | GitHub Actions | postgres:15 service container | Already wired |
| Coverage gate | pytest-cov / coverage.py | CI job step | Project-wide `fail_under = 70` |

## D-02-07-1 Root Cause (cited)

| File | Lines | Issue |
|---|---|---|
| `src/sqlery/django_sqlery/migrations/0020_daemon_lease.py` | 12-27 | `CreateModel('DaemonLease')` — table created. |
| `src/sqlery/django_sqlery/migrations/0022_delete_daemonlease_alter_jobregistry_metadata_and_more.py` | 13-73 | **Filename says "delete_daemonlease" but operations list has NO `DeleteModel`** — only 11 `AlterField`s. The `DeleteModel` was either dropped before commit or generated wrong. |
| `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py` | 38-53 | Original unconditional `CreateModel('DaemonLease')` (now commented out) — this is the "duplicate CreateModel" CONTEXT.md refers to. |
| `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py` | 4-26, 54-57 | Current state: conditional `RunPython` that checks `sqlite_master`/`information_schema` before creating. **Works for `migrate` but pytest-django's setup_databases still trips on something** — verified by deferred-items.md line 17. |

**Recommended fix (matches CONTEXT.md Decision B, targeted deletion):**

The path of least resistance is **delete the `RunPython` operation from `0023` entirely** (turn it into a `migrations.Migration` with `operations = []` and a `dependencies` entry preserving the chain), because:
1. `0022` never deleted the table.
2. `0020` created it. The state already exists.
3. `0023`'s job ("restore if missing") is only meaningful in a hypothetical world where `0022` actually deleted the table — which it doesn't.

**Alternative (if pytest-django still fails after the above):** also add a `DeleteModel('DaemonLease')` followed by a `CreateModel('DaemonLease')` inside `0023` so the migration state machine has a clean delete+recreate, matching what the filename of `0022` implied.

**Verify-after fixes:** `python manage.py makemigrations --check`, `pytest tests/integration/test_modes.py -x`, and `pytest tests/test_models.py::TestScheduledTask::test_scheduled_task_creation -x` (the canonical D-02-07-1 reproducer per `deferred-items.md:22-24`).

**No other migration drift was found** beyond this one. Recent migrations (`0024_add_timestamp_indexes`, `0025_daemoncommand`, `0026_add_shutting_down_status`) introduced in Phase 02 are clean. Phase 02 SUMMARY's mention of "DaemonCommand index/id, QueuedJob.failure_ttl" — those landed cleanly in 0025/0026; no rogue duplicates detected in a grep over `CreateModel` operations across all 26 migrations.

## TEST-12 Status

**NOT fixed.** `.github/workflows/test.yml:5-7`:

```yaml
on:
  push:
    branches: [ master ]
  pull_request:
    branches: [ master ]
```

The Phase 1 `standalone-no-django` job (`.github/workflows/test.yml:76`) was added but the trigger config was not touched. One-line two-word edit (`master` → `main` in both places). Default branch is already `main` per `git status`.

## Unit-Test Coverage Matrix

| Module | LOC | Has unit tests? | Coverage today | Key gaps |
|---|---|---|---|---|
| `core/claiming.py` | 340 | **Indirect only** | Exercised via `test_atomic_claiming.py`, `test_job_dependencies.py`, `test_ttl_retention.py` (integration-flavored) | No focused unit tests on tag concurrency, rate limit logic, TTL expiry decision, dep check; no mocked-backend tests |
| `core/worker.py` | 797 | **Indirect only** | Exercised via `test_executor.py`, `test_concurrency_and_timeout.py`, `test_serialize_worker.py` | Fork lifecycle, signal handlers (SIGUSR1/SIGALRM/SIGTERM), `_reset_db_connections()`, child crash-recovery — no unit coverage |
| `core/daemon.py` | 983 | **Indirect only** | `test_core_standalone.py` references `DaemonManager` for import sanity; `test_intervention.py` covers commands | Daemon lifecycle, scheduler integration, worker pool mgmt, lease acquire/renew/expiry, heartbeat polling — no focused tests |
| `fastapi_sqlery/backend.py` (SYNC) | 891 | **ZERO** | None — `grep -l fastapi_sqlery.backend tests/` returns no matches. (`test_sqlalchemy_async_backend.py` covers the ASYNC sibling only.) | All 30+ `DatabaseBackend` methods |
| `django_sqlery/backend.py` | 903 | **Indirect only** | `test_ttl_retention.py` uses `DjangoBackend` | All 30+ methods need direct unit coverage; today only TTL-relevant subset is exercised |
| `webhooks.py` | 269 | **ZERO** | None — `grep -rl webhooks tests/` returns nothing | HMAC signing, retry/backoff, HTTP delivery, SSRF check (deferred to Phase 4 for SEC-02 but tests live here) |

**Coverage config today:** No `[tool.coverage.*]` section in `pyproject.toml`. CI runs `pytest --cov=src/sqlery --cov-report=term-missing` (`.github/workflows/test.yml:74`) but **no threshold enforced**. Plan 03-08 adds `[tool.coverage.report] fail_under = 70` and an `[tool.coverage.run] omit = [...]` list (recommend omitting migrations, stub re-exports, `*/templates/*`).

## `tests/chaos/` Inventory

| File | Lines | Covers | TEST-03/04 coverage state |
|---|---|---|---|
| `test_worker_chaos.py` | 1-423 | SIGKILL mid-job, SIGTERM graceful, 10-worker claim race, completed-but-update-lost, slow DB, memory hog, connection-pool exhaustion, invalid status, missing task_path | **BROKEN** per self-documenting header (lines 1-20): uses removed `TaskExecutor.claim_job` API and pickles local funcs. Conceptually covers TEST-03 (timeout/crash/retry) ~partial; TEST-04 (zombie/heartbeat/lease) **not covered** — zombie-detection test is absent, lease tests are absent. |
| `test_property_based.py` | 1-308 | Hypothesis: arg serialization round-trip, random queue names, very long task paths, unicode, numeric edge cases, concurrent job creation, random cron expressions | Property-based safety net for serialization + creation. Does **not** cover TEST-03/04 directly; supports edge-case fuzzing for TEST-05 (claiming inputs). |

**TEST-03 (timeout, worker crash, retry, concurrent workers):** ~30% conceptually covered by `test_worker_chaos.py` but **0% executable** today. Plan 03-04 must (a) port to new API, (b) move task funcs to module level, (c) extend with real `subprocess.Popen` workers per CONTEXT.md Decision D.

**TEST-04 (zombie detection, heartbeat cleanup, lease expiry):** Effectively **0% covered**. New tests required; the daemon's 5-check zombie detection (CLAUDE.md, "Zombie detection") has no test coverage.

## Existing Pytest Config & Fixtures

- **Pytest config** (`pyproject.toml:139-145`):
  - `DJANGO_SETTINGS_MODULE = "tests.settings"`
  - `testpaths = ["tests"]`
  - Registered markers: only `slow`. Adding `postgres` is collision-free; `django_db` is implicitly registered via pytest-django.
- **Coverage config:** absent. Add `[tool.coverage.run]` and `[tool.coverage.report]` to `pyproject.toml` in Plan 03-08.
- **`tests/integration/conftest.py`** (existing, 558 lines) — `_build_harness(mode, integration, db)` is the fixture API the planner inherits:
  - `harness.enqueue(task_path, **kwargs) -> job_id`
  - `harness.run_mode_until_finished(job_id, timeout=30)`
  - `harness.status(job_id) -> str` (terminal: `"success"` | `"failed"`)
  - `harness.result(job_id)` — coerces `QueuedJob.output` (string) back to int
  - `harness.backend` — real backend in Django, `_StandaloneBackendSentinel` in standalone
  - Standalone cells **shell out** with `DJANGO_SETTINGS_MODULE` scrubbed and `SQLERY_FORCE_STANDALONE=1` (lines 450-476). Postgres rows gated on `SQLERY_TEST_PG_URL` env var (line 73-74, 108).
  - Collection-time skip via `pytest_collection_modifyitems` (lines 95-109). Reuse the same pattern for `postgres` marker routing.

## Phase Requirements

| ID | Description | Research support |
|---|---|---|
| TEST-01 | E2E for 6 modes × Django | Existing harness in `tests/integration/test_modes.py` covers daemon/subprocess/http-trigger/sync × django; lambda+async in dedicated files. Blocked by D-02-07-1. |
| TEST-02 | E2E for 6 modes × standalone | Harness exists (`_StandaloneHarness`). DEFERRED_TO_02_08 set is empty per `conftest.py:90`. |
| TEST-03 | Edge: timeout, crash, retry, concurrent | Rebuild `tests/chaos/test_worker_chaos.py` against new API; extend with `subprocess.Popen` workers + Hypothesis. |
| TEST-04 | Edge: zombies, stale heartbeats, lease expiry | New tests required; no existing coverage. |
| TEST-05 | Unit tests for `core/claiming.py` | Focused unit suite needed (mock backend). 340 LOC to cover. |
| TEST-06 | Unit tests for `core/worker.py` | Fork lifecycle, signal handlers, conn reset. 797 LOC. |
| TEST-07 | Unit tests for `core/daemon.py` | Lifecycle, scheduler, worker pool. 983 LOC. |
| TEST-08 | Unit tests for `fastapi_sqlery/backend.py` (sync) | **From scratch.** 30+ methods, 891 LOC. |
| TEST-09 | Unit tests for `django_sqlery/backend.py` | 30+ methods, 903 LOC. Partial via TTL retention. |
| TEST-10 | Unit tests for `webhooks.py` | **From scratch.** HMAC, retry, HTTP delivery. 269 LOC. |
| TEST-11 | Postgres tests in CI for all modes | Add `@pytest.mark.postgres` marker; CI job runs `pytest -m postgres` against the existing service container. Today only `test_atomic_claiming.py` and `test_atomic_scheduler.py` run on PG (`.github/workflows/test.yml:68`). |
| TEST-12 | CI master → main | One-line fix to `.github/workflows/test.yml:5-7`. |

## Recommended Plan Breakdown (7 plans, 3 waves)

### Wave 1 — Foundation (blocks everything else)

- **03-01: D-02-07-1 migration audit + fix** — Edit `0022` and/or `0023` per the cited root cause; verify with `makemigrations --check`, fresh-SQLite `migrate`, and `pytest tests/test_models.py::TestScheduledTask::test_scheduled_task_creation -x` plus `pytest tests/integration/test_modes.py -x`. Add a regression test that runs `setup_databases` from a clean SQLite. Touches: `src/sqlery/django_sqlery/migrations/0022_*.py`, `0023_*.py`.
- **03-02: TEST-12 master→main fix + Postgres marker scaffold** — Two-word edit to triggers. Register `postgres` marker in `pyproject.toml:143`. Add a new CI job step that runs `pytest -m postgres` against the service container. Touches: `.github/workflows/test.yml`, `pyproject.toml`.

### Wave 2 — Unit tests (parallelizable, depends on Wave 1)

- **03-03: TEST-05/06/07 — core unit tests** (`claiming`, `worker`, `daemon`). Use a `FakeBackend` (in-memory dict-backed) implementing `DatabaseBackend` ABC so tests run with no Django and no SQLAlchemy. Goal: ≥85% line coverage of these three files.
- **03-04: TEST-08/09 — backend unit tests** (sync `SQLAlchemyBackend` + `DjangoBackend`). Mirror test classes per method group: enqueue/claim/status-update/retry/cleanup/scheduled. SQLAlchemy sync uses in-memory SQLite (`StaticPool`). Django uses `@pytest.mark.django_db`.
- **03-05: TEST-10 — webhooks unit tests** (HMAC signing, retry logic with mocked `httpx`/`requests`, exponential backoff, event filtering). Note: `webhooks.py` references `requests` but it's not in `pyproject.toml` deps — flag for Phase 4 CLEAN-04 (also in REQUIREMENTS.md).

### Wave 3 — Edge cases, Postgres expansion, coverage gate

- **03-06: TEST-03/04 chaos rebuild** — Salvage what's salvageable from `tests/chaos/test_worker_chaos.py`; port to current API; add module-level task funcs; replace `multiprocessing.Process` with real `subprocess.Popen` workers running `python -m sqlery.core.worker_runner`; add zombie/heartbeat/lease tests; cap N ≤ 3 workers per test, `pytest-timeout` 60s per test. Hypothesis with `max_examples=20` (deadline=None) on chaos inputs.
- **03-07: TEST-11 — expand Postgres CI surface** — Tag all existing integration tests and the new unit/chaos suites with `@pytest.mark.postgres` where DB-engine matters. Run two CI jobs: SQLite (default; deselects `postgres`) and Postgres (only `-m postgres`). Document the matrix in CI.
- **03-08: Coverage gate** — Add `[tool.coverage.run] source = ["src/sqlery"]; omit = ["*/migrations/*", "*/templates/*", "**/stubs/*", "src/sqlery/lambda_handler.py"]` and `[tool.coverage.report] fail_under = 70; show_missing = true`. Update CI `Check code coverage` step to drop the `|| true` style and let it fail the job. Publish HTML artifact via `actions/upload-artifact@v4`. Verify baseline ≥ 70% **before** flipping the gate; if not, escalate.

(Adjust to 6 plans by merging 03-02 into 03-01, or 8 by splitting 03-04 into separate Django/SQLAlchemy plans — both reasonable. 7 is the recommended balance.)

## Common Pitfalls

1. **pytest-django `setup_databases` ≠ `manage.py migrate`.** The conditional `RunPython` in `0023` papers over a fresh `migrate` but pytest's flush/serialize semantics differ. Plan 03-01 must verify against `pytest`, not just `migrate`.
2. **Fork safety inside pytest.** `os.fork()` from within a pytest process can deadlock if the parent holds DB locks. Real-subprocess workers (`subprocess.Popen` with `start_new_session=True`) avoid this; `multiprocessing.Process` does **not** (it forks). The existing chaos tests use `multiprocessing.Process` (`test_worker_chaos.py:84,164,203`) — switch to `subprocess.Popen` per CONTEXT.md Decision D.
3. **Pickling local task functions.** `multiprocessing` pickles target+args; functions defined inside test methods cannot be pickled. Module-level task funcs only.
4. **Hypothesis flakiness on DB-backed tests.** Use `@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])`. Cap `max_examples=20-50` for chaos; 100 is fine for pure-serialization tests (already in `test_property_based.py:91`).
5. **SQLite WAL + in-memory contention.** Pytest's in-memory SQLite differs from file-based WAL config (see `apps.py` signal handler). Backend unit tests for `SQLAlchemyBackend` should use a temp file, not `:memory:`, to exercise WAL paths.
6. **Coverage of stub files inflates baseline.** The 24 backward-compat stubs (CLEAN-01) are mostly re-exports; they will report 100% from a single import. Omit `**/stubs/*` and any `*_compat.py` files in `[tool.coverage.run] omit`.
7. **Postgres marker on shared fixtures.** A test marked `postgres` must not also use `@pytest.mark.django_db(transaction=True)` without ensuring its fixtures don't conflict — pytest-django's transaction fixture defaults to the configured DB. Plan 03-07 needs a `db_engine` fixture that routes both.
8. **CI artifact size for coverage HTML.** `--cov-report=html` produces ~MBs. Use `actions/upload-artifact@v4` with `retention-days: 14`.
9. **`requests` not declared.** `webhooks.py:1` (per import) uses `requests` but it's not in `pyproject.toml`. Plan 03-05 should mock the HTTP client; flag the missing dep to Phase 4 CLEAN-04 (already tracked).
10. **Django Lambda smoke quirk** (per CONTEXT.md note 4): `claim_job` requires Worker-row registration; Lambda passes `job_id` explicitly. Tests must preserve that behavior — document as known limitation, don't "fix" it.

## Environment Availability

| Dependency | Required by | Available | Version | Fallback |
|---|---|---|---|---|
| pytest | All test work | ✓ (dev extra) | ≥7.4 | — |
| pytest-django | TEST-01/05-09 | ✓ | ≥4.5 | — |
| pytest-cov | 03-08 coverage gate | ✓ | ≥4.1 | — |
| pytest-timeout | 03-06 chaos timeouts | ✓ | ≥2.2 | — |
| hypothesis | 03-06 + property tests | ✓ | ≥6.92 | — |
| postgres:15 (CI service) | TEST-11 | ✓ | wired (`.github/workflows/test.yml:18-30`) | — |
| `requests` | webhooks.py (currently) | ✗ in pyproject | — | Mock in tests; CLEAN-04 owns the real fix |

**No blocking missing deps.** All test infrastructure is already a transitive dev dep.

## Validation Architecture

| Property | Value |
|---|---|
| Framework | pytest 7.4+ with pytest-django 4.5+ |
| Config | `pyproject.toml:139-145` (`[tool.pytest.ini_options]`) |
| Quick run | `uv run pytest tests/ -x --ignore=tests/chaos` |
| Full suite | `uv run pytest tests/ --cov=src/sqlery --cov-report=term-missing` |
| Chaos | `uv run pytest tests/chaos/ --timeout=60` |
| Postgres-only | `uv run pytest -m postgres` (after Plan 03-02) |

### Phase Requirements → Test Map

| Req | Behavior | Test type | Command | Exists? |
|---|---|---|---|---|
| TEST-01 | E2E 6 modes × Django | integration | `pytest tests/integration/test_modes.py -k django` | ✅ (blocked by D-02-07-1) |
| TEST-02 | E2E 6 modes × standalone | integration | `pytest tests/integration/test_modes.py -k standalone` | ✅ (blocked by D-02-07-1) |
| TEST-03 | timeout/crash/retry/concurrent | chaos | `pytest tests/chaos/test_worker_chaos.py` | ❌ broken, rebuild in 03-06 |
| TEST-04 | zombie/heartbeat/lease | chaos | `pytest tests/chaos/test_lease_zombie.py::*` | ❌ Wave 3 |
| TEST-05 | claiming unit | unit | `pytest tests/unit/test_claiming.py` | ❌ Wave 2 |
| TEST-06 | worker unit | unit | `pytest tests/unit/test_worker.py` | ❌ Wave 2 |
| TEST-07 | daemon unit | unit | `pytest tests/unit/test_daemon.py` | ❌ Wave 2 |
| TEST-08 | sync SQLAlchemy backend unit | unit | `pytest tests/unit/test_sqlalchemy_backend_sync.py` | ❌ Wave 2 |
| TEST-09 | Django backend unit | unit | `pytest tests/unit/test_django_backend.py` | ❌ Wave 2 |
| TEST-10 | webhooks unit | unit | `pytest tests/unit/test_webhooks.py` | ❌ Wave 2 |
| TEST-11 | Postgres CI all modes | CI | `pytest -m postgres` | ❌ Wave 3 |
| TEST-12 | master→main | CI config | manual diff | ❌ Wave 1 |

### Wave 0 Gaps

- [ ] `tests/unit/` directory does not exist — create in Plan 03-03 with a shared `conftest.py` exposing `FakeBackend`.
- [ ] No `FakeBackend` implementing `DatabaseBackend` ABC — required for core unit tests.
- [ ] `pytest.ini_options.markers` lacks `postgres` — Plan 03-02 adds it.
- [ ] No `[tool.coverage.*]` section in `pyproject.toml` — Plan 03-08 adds it.

## Project Constraints (from CLAUDE.md)

- Python 3.10+ minimum (use `X | None` syntax in any new test helpers).
- No new runtime deps. Tests may use existing `dev` extras only.
- Fork safety: real-subprocess tests must close DB connections before fork (mirroring `_reset_db_connections()`).
- Dead code policy: do **not** delete the broken `test_worker_chaos.py` — comment-and-date-mark per `feedback_dead_code.md` if any test is unsalvageable, or port it.
- GSD workflow: all edits through `/gsd-execute-phase` once plans land.

## Assumptions Log

| # | Claim | Section | Risk if wrong |
|---|---|---|---|
| A1 | Removing `0023`'s `RunPython` (turning it into a no-op) is sufficient to fix D-02-07-1 because `0020`'s `CreateModel` is uncontested. | D-02-07-1 root cause | If pytest-django reconstructs state from migration files (not actual DB), the `restore` operation may be load-bearing for the autodetector — may need the alternative delete+recreate path instead. |
| A2 | Baseline coverage is already ≥70% — Phase 1/2 work likely got us there. | Wave 3, Plan 03-08 | If baseline <70%, the gate flip fails CI; Plan 03-08 must run a measurement step first and back off the threshold or add tests. |
| A3 | `subprocess.Popen` workers can run inside pytest without process-group conflicts when `start_new_session=True`. | Pitfall #2 | If CI's containerized environment restricts new sessions, fall back to `setsid` invocations. |

## Open Questions

1. **Will the conditional `RunPython` in `0023` need to remain for users who already migrated to a state where 0023 was applied unconditionally?** — Plan 03-01 should keep `0023` as a no-op migration (not delete the file) to preserve migration-graph compatibility with deployed databases (CLAUDE.md: backward compatibility constraint). Confirm with user / Decision B clarification.
2. **Baseline coverage number?** Run `pytest --cov=src/sqlery --cov-report=term` once at the start of Wave 3 and capture in 03-08 PLAN.
3. **`@pytest.mark.postgres` vs reusing `slow`?** CONTEXT.md flags this as ASSUMED/negotiable. Recommend keeping them separate: `slow` is runtime-based, `postgres` is engine-based; a test can be both.

## Sources

### Primary (HIGH confidence — direct file reads)
- `src/sqlery/django_sqlery/migrations/0020_daemon_lease.py:12-27`, `0022_*.py:13-73`, `0023_restore_daemonlease.py:4-57`
- `.github/workflows/test.yml:1-131`
- `pyproject.toml:1-154`
- `tests/integration/conftest.py:1-558`
- `tests/chaos/test_worker_chaos.py:1-423`, `test_property_based.py:1-308`
- `.planning/phases/02-execution-modes/02-VERIFICATION.md`, `02-07-SUMMARY.md`, `deferred-items.md`
- `.planning/phases/03-testing-ci/03-CONTEXT.md`
- `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `CLAUDE.md`

### Tooling references (CITED)
- pytest-django `setup_databases` semantics: https://pytest-django.readthedocs.io/en/latest/database.html
- pytest-cov / coverage.py `fail_under`: https://coverage.readthedocs.io/en/latest/config.html
- Hypothesis health-check suppression: https://hypothesis.readthedocs.io/en/latest/healthchecks.html

## Metadata

**Confidence breakdown:**
- D-02-07-1 root cause: HIGH (operations list grepped; filename ↔ contents mismatch confirmed)
- Unit-test coverage matrix: HIGH (grep across `tests/`)
- TEST-12 status: HIGH (workflow file read directly)
- Chaos inventory: HIGH (file headers self-document the breakage)
- Plan breakdown: MEDIUM (sizing depends on baseline coverage, A2)

**Research date:** 2026-05-14
**Valid until:** ~2026-06-14 (stable areas; rerun if a new migration lands)
