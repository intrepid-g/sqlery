# Phase 03 — Testing & CI: Plan Index

**Created:** 2026-05-14
**Plans:** 8
**Waves:** 3

## Wave Map

| Wave | Plans | Parallel? | Autonomous |
|------|-------|-----------|------------|
| 1 | 03-01, 03-02 | yes (no file overlap) | both have checkpoints (not autonomous) |
| 2 | 03-03, 03-04, 03-05 | yes (no file overlap) | all autonomous |
| 3 | 03-06, 03-07, 03-08 | 03-06 parallel; 03-07 + 03-08 sequential (both edit `.github/workflows/test.yml`) | 03-06 auto; 03-07/03-08 have checkpoints |

## Dependency DAG

```
03-01 (migration fix) ─┐
                       ├─> 03-03 (core unit)         ─┐
                       ├─> 03-04 (backend unit)      ─┼─> 03-07 (PG CI expansion) ─> 03-08 (coverage gate)
                       └─> 03-06 (chaos rebuild)     ─┤
                                                     ─┘
03-02 (master→main +   ─> 03-07 (PG CI expansion)
        postgres rail)

03-05 (webhooks unit) [no deps] ─> 03-08 (coverage gate)
```

Explicit `depends_on` (from frontmatter):

| Plan | depends_on |
|------|-----------|
| 03-01 | [] |
| 03-02 | [] |
| 03-03 | [01] |
| 03-04 | [01] |
| 03-05 | [] |
| 03-06 | [01, 03] |
| 03-07 | [01, 02, 03, 04, 06] |
| 03-08 | [03, 04, 05, 06, 07] |

## Requirement → Plan Matrix

| Req | Plan | Notes |
|-----|------|-------|
| TEST-01 (E2E Django × 6 modes) | 03-01 (unblock), 03-07 (PG matrix) | E2E suites already exist in `tests/integration/test_modes.py`; 03-01 unblocks setup_databases. |
| TEST-02 (E2E standalone × 6 modes) | 03-01 (unblock), 03-07 (PG matrix) | Same harness; 03-07 adds PG variants. |
| TEST-03 (timeout/crash/retry/concurrent) | 03-06 | Real-subprocess + Hypothesis. New file `test_subprocess_chaos.py`. |
| TEST-04 (zombies/heartbeats/lease) | 03-06 | New file `test_lease_zombie.py`. |
| TEST-05 (core/claiming unit) | 03-03 | `tests/unit/test_claiming.py` against FakeBackend. |
| TEST-06 (core/worker unit) | 03-03 | `tests/unit/test_worker.py` with mocked fork. |
| TEST-07 (core/daemon unit) | 03-03 | `tests/unit/test_daemon.py`. |
| TEST-08 (sync SQLAlchemyBackend unit) | 03-04 | `tests/unit/test_sqlalchemy_backend_sync.py` (was ZERO coverage). |
| TEST-09 (DjangoBackend unit) | 03-04 | `tests/unit/test_django_backend.py`. |
| TEST-10 (webhooks unit) | 03-05 | `tests/unit/test_webhooks.py` (was ZERO coverage). |
| TEST-11 (Postgres CI all modes) | 03-02 (scaffold), 03-07 (expansion) | Marker registered + CI rail in 03-02; tags + strict step in 03-07. |
| TEST-12 (CI master → main) | 03-02 | One-line two-word edit. |
| (no req — gate enforcement) | 03-08 | CONTEXT decision C; not a numbered REQ. `requirements: []` in 03-08 frontmatter. |

**Coverage check:** All 12 TEST-* requirement IDs appear in at least one plan. ✓

## File Ownership (parallelism check)

Same-wave overlap audit:

**Wave 1** (03-01, 03-02):
- 03-01: migrations + new regression test
- 03-02: `.github/workflows/test.yml`, `pyproject.toml`
- Overlap: NONE → parallel-safe. ✓

**Wave 2** (03-03, 03-04, 03-05):
- 03-03: `tests/unit/{__init__,conftest,test_claiming,test_worker,test_daemon}.py`
- 03-04: `tests/unit/test_{sqlalchemy_backend_sync,django_backend}.py`
- 03-05: `tests/unit/test_webhooks.py`
- Overlap: NONE → parallel-safe. ✓ Note: 03-03 creates `tests/unit/conftest.py`; 03-04 and 03-05 depend on it being present at runtime BUT do not modify it. RESEARCH-grade nuance: if Wave 2 runs in parallel worktrees, 03-04 and 03-05 must `mkdir -p tests/unit` and write their files without assuming conftest.py exists — verify by structure-check in executor.
  - **MITIGATION:** declared `depends_on: [01]` only (not on 03-03) is correct for sequencing of the work but executors should not import from `tests.unit.conftest` in 03-04 / 03-05 to avoid runtime coupling. Tests in 03-04 / 03-05 use their own fixtures (Django ORM / mocked HTTP) — verified in their plan content.

**Wave 3** (03-06, 03-07, 03-08):
- 03-06: `tests/chaos/*`
- 03-07: `.github/workflows/test.yml`, `tests/integration/conftest.py`, `tests/integration/test_modes.py`, `tests/chaos/test_subprocess_chaos.py`, `tests/chaos/test_lease_zombie.py`, `tests/unit/test_django_backend.py`, `tests/unit/test_sqlalchemy_backend_sync.py`
- 03-08: `pyproject.toml`, `.github/workflows/test.yml`
- Overlaps: 03-07 vs 03-08 share `.github/workflows/test.yml`. 03-07 vs 03-06 share `tests/chaos/test_subprocess_chaos.py` and `test_lease_zombie.py`. 03-07 vs 03-04 share `tests/unit/test_django_backend.py` and `test_sqlalchemy_backend_sync.py` (but 03-04 is Wave 2 — sequential, no conflict).
- **Forced sequencing within Wave 3:** 03-06 → 03-07 → 03-08. Effectively Wave 3 is serial. Acceptable: 03-08 is the close-out, 03-07 needs 03-06's chaos files to tag, 03-06 has no upstream Wave 3 dep.

## Cross-Plan Notes

1. **03-01 must land before 03-03 and 03-04** (`@pytest.mark.django_db` tests require setup_databases to work).
2. **03-02 must land before 03-07** (postgres marker must be registered + CI rail in place).
3. **03-06's CONTEXT deviation** (rebuild rather than extend the broken file) is called out in 03-06 Objective and respects CLAUDE.md `feedback_dead_code` policy (legacy file preserved + skip-marked).
4. **03-08's decision rule** halts the gate flip if baseline coverage < 70% and surfaces the gap to the user rather than silently lowering the threshold.
5. **[ASSUMED] flags** for plan-checker to verify:
   - 03-02 Task 1: marker name `postgres` (CONTEXT.md marks as ASSUMED, planner kept it; rename trivially if user disagrees).
   - 03-08 Task 1: baseline coverage ≥ 70% (RESEARCH A2; not directly verified by planner — Task 1 makes this measurable).
   - 03-01 Task 1: the "operations=[]" fix is sufficient for pytest-django (RESEARCH A1; Task 3 checkpoint includes fallback path to delete+recreate).

## Open Items at Plan-Close

- None blocking. All 12 requirement IDs are mapped. All locked CONTEXT decisions are addressed (with the 03-06 deviation explicitly justified).

---
*Index created: 2026-05-14*
