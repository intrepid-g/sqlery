# Phase 03 — Testing & CI: Context

**Phase:** 03-testing-ci
**Created:** 2026-05-14
**Source of scope:** ROADMAP.md Phase 3 + REQUIREMENTS.md (TEST-01..12)

## Canonical refs

- `.planning/ROADMAP.md` — Phase 3 definition (lines 51–62)
- `.planning/REQUIREMENTS.md` — TEST-01..12 (lines 45–56)
- `.planning/phases/02-execution-modes/02-VERIFICATION.md` — Phase 2 close-out
- `.planning/phases/02-execution-modes/deferred-items.md` — D-02-07-1 routed here
- `.planning/PROJECT.md` — execution mode matrix
- `CLAUDE.md` — fork safety, Python 3.10+, dead-code policy

## Decisions (locked)

### A. Postgres test infrastructure — GitHub Actions service container

CI runs Postgres tests against the existing `postgres:15` service container block in `.github/workflows/test.yml`. No new dependency. Devs run Postgres locally via Docker as documented in PROJECT.md.

**Why:** Zero new deps; the service container is already wired; uniform CI shape across modes.
**How to apply:** TEST-11 expansion uses pytest markers (`@pytest.mark.postgres`) to opt tests into the PG job; the SQLite job skips them.

### B. D-02-07-1 fix — targeted duplicate-CreateModel deletion

The bug: pytest's `setup_databases` fails with `sqlery_daemon_lease already exists` because a later migration re-declares `DaemonLease`. Fix: audit the `django_sqlery/migrations/` chain, identify the migration that re-creates `DaemonLease`, and remove that duplicate operation. No squash; preserve history for users already on the chain.

**Why:** Lowest-risk targeted fix; squashing breaks users with deployed databases.
**How to apply:** This is Plan 03-01 (foundation). Audit + minimal edit + assert via `python manage.py makemigrations --check` and a passing `pytest tests/integration/test_modes.py`. Recovery test in Phase 3's integration suite locks the regression.

### C. Coverage gate — hard 70% project-wide

CI fails if line coverage drops below 70% across the whole project. Use `pytest-cov` (already a dep) + `coverage.py`'s `[tool.coverage.report] fail_under = 70` in `pyproject.toml`. Coverage HTML report published as a CI artifact.

**Why:** Gives a real signal without forcing busywork. Project-wide rather than per-module keeps the gate honest about the integration surface.
**How to apply:** Plan 03-08 adds the gate; before flipping it on, make sure the baseline ≥70% (Phase 1/2 work likely already there but verify).

### D. Concurrent-worker / chaos strategy — real subprocesses + Hypothesis

Concurrent-worker, zombie-detection, and crash-recovery tests use real `subprocess.Popen` workers against a shared DB, with Hypothesis to randomize job timing/payload shapes. Build on the existing `tests/chaos/` harness — extend, don't replace.

**Why:** Threads-and-mocks won't exercise the fork-safety paths that Phase 1 hardened. Hypothesis is already in deps.
**How to apply:** Plans 03-04 (edge cases: timeout/crash/retry) and 03-05 (edge cases: zombies/heartbeat/lease) extend `tests/chaos/`. Use small N (≤3 workers) per test to keep CI runtimes reasonable.

## Specifics & gotchas

1. **TEST-12 (CI master → main fix)** — PROJECT.md flags CI workflow currently triggers on `master`. Verify whether Phase 1 inadvertently fixed this when the standalone-no-django job was added; if not, it's a one-line fix in `.github/workflows/test.yml`.
2. **D-02-07-1 must land first.** Every integration-test plan depends on `setup_databases` working.
3. **Phase 1 CI human-verify checkpoint** for `standalone-no-django` still open — not blocking; orthogonal to Phase 3.
4. **Phase 2 deferred lambda quirk** — Django Lambda smoke passes `job_id` explicitly because `claim_job` requires Worker-row registration. The integration suite should NOT regress that behavior; document it as a known limitation.
5. **TEST-08 (SQLAlchemyBackend unit tests)** — the SYNC backend in `fastapi_sqlery/backend.py`. Phase 2 added unit tests for the async variant. The sync backend's coverage today is the gap.

## Deferred (not Phase 3)

- Mutation testing (mutmut/cosmic-ray) — separate hardening pass.
- Performance/load benchmarks — out of scope; Phase 3 is correctness.
- Test parallelization (`pytest-xdist`) — defer until CI runtime becomes painful.

## Open items for the researcher

1. Audit `src/sqlery/django_sqlery/migrations/` to identify the migration that re-declares `DaemonLease` (the D-02-07-1 root cause).
2. Verify TEST-12 status (is `.github/workflows/test.yml` already on `main`? Or was that the trigger config?).
3. Map current unit-test coverage by module — which of `core/claiming.py`, `core/worker.py`, `core/daemon.py`, `fastapi_sqlery/backend.py` (sync), `django_sqlery/backend.py`, `webhooks.py` have ZERO unit tests today vs partial.
4. Inventory existing `tests/chaos/` files and decide which existing tests cover TEST-03/04 already.

## Locked vs negotiable

| Item | Status |
|---|---|
| GitHub Actions service-container Postgres | **LOCKED** |
| Targeted duplicate-CreateModel deletion for D-02-07-1 | **LOCKED** |
| Hard 70% project-wide coverage gate | **LOCKED** |
| Real-subprocess + Hypothesis chaos tests, extending tests/chaos/ | **LOCKED** |
| Pytest marker name for Postgres opt-in (`@pytest.mark.postgres`) | **ASSUMED** — planner may rename |
| Coverage threshold = 70% | **LOCKED** but flagged for adjustment if baseline is materially above |
