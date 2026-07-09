# Phase 11: Parity-Gated Tests & CI - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning
**Mode:** Auto-generated (test/CI infrastructure phase; approach pinned by existing conventions + REQUIREMENTS PARITY-01..05 — mechanism is Claude's discretion)

<domain>
## Phase Boundary

Prove that failover, single-firing, drift correctness, and bare-worker scheduling behave **identically across the full `{Django, standalone} × {SQLite, Postgres}` matrix**, and enforce that as a first-class, CI-gated acceptance check. Four behavioral tests plus the CI wiring:

1. **PARITY-01 — Failover:** Killing the lease leader causes another worker to take over scheduling within one lease TTL — across the full matrix.
2. **PARITY-02 — No-duplicate:** Two simultaneous leaders fire a given cron task exactly once.
3. **PARITY-03 — Atomic-advance/drift:** `next_run_at` advances correctly (no drift, no skip) across several ticks.
4. **PARITY-04 — Bare-worker E2E:** Cron fires with only `sqlery-worker` processes and no daemon.
5. **PARITY-05 — Matrix gate:** Every behavioral test asserts identical outcomes across all four matrix cells and the CI config enforces the full grid as an acceptance gate (Postgres cells actually run in CI, not just skip locally).

Scope: tests + CI config only. The behaviors under test were built in Phases 8–10; this phase does not change production scheduling logic. It DOES close the verification gaps deferred to "Phase 11" by Phases 8/9/10 (Postgres-path proofs that auto-skipped locally).
</domain>

<decisions>
## Implementation Decisions

### Locked (REQUIREMENTS + existing conventions)
- Parity is asserted across `{Django, standalone} × {SQLite, Postgres}` — all four cells, identical expected outcomes.
- Reuse the EXISTING test infrastructure, do not invent a parallel one:
  - `@pytest.mark.postgres` marker — Postgres cells carry it via `pytest.param('postgres', marks=[pytest.mark.postgres])` on the `db` axis; SQLite cells stay unmarked.
  - The `db_engine` fixture (standalone PG mirror) and the integration `harness` (`(mode, integration, db)` parametrization) in `tests/integration/conftest.py`.
  - CI rails in `.github/workflows/test.yml`: default rail runs `-m "not postgres"`, the dedicated Postgres rail runs `-m postgres` against the `postgres:15` service. Postgres cells gated on `SQLERY_TEST_PG_URL`.
- Tests must SKIP (not silently pass) when a required backend is unavailable — but the CI grid must ensure each cell actually executes somewhere (Postgres rail covers the PG cells).
- The CI gate is first-class: a failing parity cell fails the build (not advisory).

### Claude's Discretion (mechanism — resolve during planning)
- How to simulate failover (PARITY-01) and two-leader overlap (PARITY-02): prefer in-process lease manipulation (set `expires_at` directly / drive two backend instances) for the SQLite inner-loop cells — matching the existing integration conftest's in-process `daemon once=True` precedent — and reserve real-subprocess variants (marked `slow`) for where genuine process isolation is required.
- Whether the bare-worker E2E (PARITY-04) spawns real `sqlery-worker` subprocesses or drives `WorkerProcess.run` in a bounded/once mode; reuse any existing one-shot/`--once` harness entry points.
- Test file layout (extend existing `tests/test_atomic_scheduler.py` / `tests/chaos/` vs a new `tests/test_parity_scheduler.py`) — pick what fits the existing structure and avoids duplicate coverage.
- Exact CI job/step shape to guarantee all four cells run (may extend the matrix or add explicit steps); keep both Django and standalone exercised on both engines.
</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets / Patterns
- **Markers/config:** `pyproject.toml` `[tool.pytest...] markers` defines `postgres: requires a running PostgreSQL service` (line ~146). `postgres` optional-dep group exists.
- **Integration harness:** `tests/integration/conftest.py` — parametrized `(mode, integration, db)` matrix, `harness` object (`enqueue`, `run_mode_until_finished`, `status`, `result`, `backend`), in-process `DaemonManager._run_daemon(once=True)` for SQLite cells, `@pytest.mark.slow` + `SQLERY_TEST_PG_URL` gating for Postgres. The `db_engine` fixture is the lightweight parametrize-on-call counterpart for unit suites.
- **Cross-backend lease/zombie test precedent:** `tests/chaos/test_lease_zombie.py` — `TestLeaseExpiry / TestLeaseContention / TestLeaseGracefulRelease` hit `claim/renew/release_queue_leases`; SKIP if the active backend lacks leases (now implemented in both after Phase 8).
- **Phase 8/9/10 tests to build on:** `tests/unit/test_sqlalchemy_backend_sync.py` (standalone lease lifecycle + PG mirror that auto-skips), `tests/unit/test_worker.py` `TestWorkerSchedulerElection` (election wiring), `tests/test_atomic_scheduler.py` + `tests/test_scheduler_drift_jitter.py` + `tests/test_core_standalone.py` (CRON-01..04 single-firing/drift/jitter — currently proven on SQLite; PG concurrency cells auto-skip).
- **FakeBackend** (`tests/unit/conftest.py`) now implements real lease semantics + `advance_scheduled_task_if_due` — useful for fast deterministic election/overlap simulation, but PARITY tests need the REAL backends to prove cross-mode identity.

### Integration Points
- Production paths under test (unchanged this phase): `src/sqlery/core/scheduler.py` (`run_due_tasks`, `advance_scheduled_task_if_due` callers), `src/sqlery/core/worker.py` (election lifecycle, lease renewal during long jobs), `src/sqlery/core/daemon.py`, the lease methods in both backends.
- CI: `.github/workflows/test.yml` (matrix `python-version × django-version`, `postgres:15` service, default `-m "not postgres"` rail + dedicated `-m postgres` rail, plus a `standalone-no-django` job).

### Carry-forward from earlier phases
- Phase 8/9/10 each deferred their Postgres-path / cross-matrix proof to "Phase 11" — those auto-skipped PG cells (e.g. `test_expired_lease_taken_over_under_concurrent_lock`, atomic single-fire-under-overlap PG cell) must be made to actually run in the Postgres CI rail here.
- The four parity behaviors map onto Phases: PARITY-01/02 ↔ Phase 9 election + Phase 10 CRON-04; PARITY-03 ↔ Phase 10 CRON-01/02; PARITY-04 ↔ Phase 9 ELECT-04.
</code_context>

<specifics>
## Specific Ideas

Parametrize each of the four behavioral tests over an `(integration, db)` axis = {Django, standalone} × {SQLite, Postgres}, attaching `@pytest.mark.postgres` only to the Postgres params so the existing CI rails route them correctly. Assert the SAME outcome in every cell (the test body is backend-agnostic, delegating to the active `DatabaseBackend`). Ensure the CI Postgres rail exercises both Django and standalone PG cells so PARITY-05 is genuinely enforced, not skipped.
</specifics>

<deferred>
## Deferred Ideas

- Worker takeover of scheduling while a daemon is up (election always defers to daemon) — deferred (milestone-level).
- `WORKER_SCHEDULER_ELIGIBLE` opt-out knob — deferred.
- The Phase 10 review's WR-03 (jitter serial-sleep perf) and WR-04 (legacy `scheduler_tasks.py` non-atomic path) — out of scope; tracked in 10-REVIEW.md for a future follow-up.
</deferred>
