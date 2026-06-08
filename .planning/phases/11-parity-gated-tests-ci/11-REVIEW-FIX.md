---
phase: 11-parity-gated-tests-ci
fixed_at: 2026-06-08T12:30:00Z
review_path: .planning/phases/11-parity-gated-tests-ci/11-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 2
status: all_fixed
---

# Phase 11: Code Review Fix Report

**Fixed at:** 2026-06-08T12:30:00Z
**Source review:** .planning/phases/11-parity-gated-tests-ci/11-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope (Critical + Warning): 5 (CR-01, WR-01, WR-02, WR-03, WR-04)
- Fixed: 5
- Skipped: 2 (IN-01, IN-02 — Info, out of scope)

## Fixed Issues

### CR-01 (BLOCKER): Standalone parity CI step ran Django-ORM PG cells under a forced uninitialized standalone backend

**Files modified:** `.github/workflows/test.yml`, `pyproject.toml`, `tests/test_core_standalone.py`, `tests/test_parity_scheduler.py`, `tests/chaos/test_lease_zombie.py`
**Commit:** 3aea641
**Applied fix:**
- Introduced a dedicated `standalone_pg` pytest marker (registered in `pyproject.toml` `[tool.pytest.ini_options] markers`).
- Tagged the three genuinely-standalone PG cells with `@pytest.mark.standalone_pg`:
  - `tests/test_core_standalone.py::TestStandaloneAdvanceScheduledTaskPostgres` (real PG engine via `pg_standalone_backend`)
  - `tests/chaos/test_lease_zombie.py::TestStandaloneLeaseFailoverPostgres` (real PG engine via `pg_standalone_backend`)
  - `tests/test_parity_scheduler.py::TestParityBareWorkerE2E::test_bare_worker_standalone_real_process` (real no-Django subprocess; `standalone_pg` marks both `db` params but only the `[postgres]` param also carries `postgres`, so the selector picks only it)
- Re-scoped the "Run standalone-mode parity suite with PostgreSQL" step to `-m "postgres and standalone_pg"`, replacing the broad three-file `-m postgres` run (old commands commented out with `# Old:` per project rules, not deleted). The selector now excludes the Django-mode PG cells (`TestParityFailover`, `TestCronSemanticsHardeningPostgres`) that previously raised `RuntimeError("Database not initialized")` under the forced standalone backend.
- Pointed the precheck/run at `tests/chaos/test_lease_zombie.py` (where `TestStandaloneLeaseFailoverPostgres` actually lives) instead of `tests/test_atomic_scheduler.py` — the REVIEW.md suggestion mislocated that class in `test_core_standalone.py`. The fix was adapted to the real source layout.

**Verification:** `pytest -m "postgres and standalone_pg" --co` collects exactly the 4 standalone items (3 cells, bare-worker only its `[postgres]` param) and deselects all 21 others; a plain `-m postgres --co` was confirmed to include the Django-mode cells the old step would have errored on. The Django × Postgres cells remain covered by the unforced "Run @pytest.mark.postgres suite" step.

### WR-01: `_lease_supported` swallowed the engine-not-initialized RuntimeError

**Files modified:** `tests/test_parity_scheduler.py`, `tests/chaos/test_lease_zombie.py`
**Commit:** 3aea641 (committed together with CR-01 — see note below)
**Applied fix:** Added an `except RuntimeError: return False` branch before the blanket `except Exception: return True` in both copies of `_lease_supported`, with `# Old:` comment preserving the original blanket-except behavior. A genuine "Database not initialized"/engine error now reports "unsupported" (clean skip) rather than "supported" followed by a misleading downstream failure.

### WR-02: Django `@pytest.mark.postgres` cells ran against in-memory SQLite

**Files modified:** `tests/settings.py`
**Commit:** 7151992
**Applied fix:** Made `DATABASES["default"]` env-driven. When `SQLERY_TEST_PG_URL` (preferred, matches the PG rail env) or `DATABASE_URL` is a `postgres://`/`postgresql://`(+driver) DSN, it is parsed (via `urllib.parse.urlparse`) into Django's `ENGINE`/`NAME`/`USER`/`PASSWORD`/`HOST`/`PORT`. Otherwise it falls back to the original in-memory SQLite. The original hardcoded block is preserved as a `# Old:` comment per project rules. Default (SQLite-only) test runs are unchanged.

### WR-03: `--collect-only` precheck did not guarantee the standalone cells specifically

**Files modified:** `.github/workflows/test.yml` (same change as CR-01)
**Commit:** 3aea641
**Applied fix:** Resolved structurally by the CR-01 scoping. The precheck now runs `pytest -m "postgres and standalone_pg" --co -q` against the three files, so a non-empty (exit != 5) collection means the standalone cells specifically are present — a missing/renamed standalone cell now fails the build, restoring the gate's intended meaning.

### WR-04: `TestLeaseExpiry` used a real `time.sleep(1.5)`

**Files modified:** `tests/chaos/test_lease_zombie.py`
**Commit:** 3aea641 (committed together with CR-01 — see note below)
**Applied fix:** Replaced the real `time.sleep(1.5)` aging of a 1s lease with a PAST `expires_at` write (`DaemonLease.objects.filter(queue_name="chaos-q").update(expires_at=timezone.now() - timedelta(seconds=5))`), mirroring the parity cells' "no real TTL sleep" convention and removing 1.5s of wall-clock per run. Added the diagnostic message `"expired lease must be re-claimable by daemon-b"` to the takeover assertion. The old `time.sleep` lines are preserved as `# Old:` comments.

## Skipped Issues

### IN-01: Unused local variables in pre-existing scheduler tests

**File:** `tests/test_atomic_scheduler.py:149-150, 299-301`
**Reason:** skipped — Info severity, out of `critical_warning` scope. Pre-existing dead bindings, not introduced this phase.
**Original issue:** `locked_task` assigned and never used in `claim_task`/`hold_lock`.

### IN-02: `prior_scheduled` assigned but unused in drift tests

**File:** `tests/test_atomic_scheduler.py:720`, `tests/test_core_standalone.py` (drift loops)
**Reason:** skipped — Info severity, out of `critical_warning` scope. Minor readability noise.
**Original issue:** `prior_scheduled = task.next_run_at` immediately copied and never read again.

## Notes

- **Commit grouping:** `gsd-tools query commit --files` stages whole files. Because CR-01's `standalone_pg` marker, WR-01's `except RuntimeError` branch, and WR-04's sleep removal all co-reside in `tests/test_parity_scheduler.py` and `tests/chaos/test_lease_zombie.py`, those three findings landed together in commit `3aea641` rather than as three separate atomic commits. All fixes are present and individually attributable via their `# ... (11-REVIEW):` inline tags. WR-02 (`tests/settings.py`, no overlap) is isolated in commit `7151992`.
- **Adaptation from REVIEW.md:** CR-01's suggested node-ID selection referenced `tests/test_core_standalone.py::TestStandaloneLeaseFailoverPostgres`, but that class actually lives in `tests/chaos/test_lease_zombie.py`. The marker-based approach (explicitly blessed by the prompt) was chosen instead, which is robust to renames/moves and was verified against the real layout.
- **Verification gate:** `uv run --active pytest tests/unit tests/test_atomic_scheduler.py tests/test_core_standalone.py tests/test_parity_scheduler.py tests/chaos/test_lease_zombie.py -m "not postgres and not slow" -q` → 454 passed, 6 skipped, 3 xfailed. Syntax checks (ast.parse) and YAML parse pass for all edited files. No new ruff/black violations introduced by the edits (pre-existing `make_scheduled_task`/`datetime` unused-import warnings and a pre-existing black docstring-blank-line in `settings.py` were left untouched as out of scope).

---

_Fixed: 2026-06-08T12:30:00Z_
_Fixer: gsd-code-fixer_
_Iteration: 1_
