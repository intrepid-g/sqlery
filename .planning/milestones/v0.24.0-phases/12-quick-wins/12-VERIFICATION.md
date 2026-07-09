---
phase: 12-quick-wins
verified: 2026-06-11T00:00:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
---

# Phase 12: quick-wins Verification Report

**Phase Goal:** The hot claim path scans only pending rows, cleanup never bursts unbounded DELETEs, and the library floor is Python 3.13 — shippable immediately, independent of everything else.
**Verified:** 2026-06-11
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | New partial index `sqlery_job_pending_idx` used by claim path; old full composite index gone (commented out) | VERIFIED | `models.py:592` old index commented out; `models.py:593-597` partial index present with `fields=["queue_name", "-priority", "created_at"]` and `condition=Q(status="queued")`; claim path at `backend.py:884` filters `status="queued"` ensuring index use |
| 2 | Cleanup of a large backlog is batched (CLEANUP_BATCH_SIZE=500), never holds lock unbounded, never deletes a row claimed mid-loop, and loop terminates | VERIFIED | Django backend: `backend.py:27` `CLEANUP_BATCH_SIZE = 500`, batched loop at lines 497-512 with `query.order_by("id").values_list("id", flat=True)[:CLEANUP_BATCH_SIZE]`, status re-check via `query.filter(id__in=ids).delete()`, no-progress guard at line 507, `time.sleep(0.1)` at line 512; FastAPI backend: same pattern at lines 737-764 |
| 3 | SQLite path untouched (no behavioral change to SQLite job processing) | VERIFIED | Migration 0028 wraps `AddIndexConcurrently`/`RemoveIndexConcurrently` in `SafeAdd/SafeRemoveIndexConcurrently` subclasses (lines 19-51) that check `schema_editor.connection.vendor != "postgresql"` and return early; SQLite CI path is a confirmed no-op |
| 4 | `requires-python = ">=3.13"` in pyproject.toml; CI matrix updated (3.10/3.11/3.12 dropped or commented); PROJECT.md constraint updated | VERIFIED | `pyproject.toml:11` active `requires-python = ">=3.13"` (line 10 with `>=3.10` commented out); tomllib parse confirms no 3.10/3.11/3.12 classifiers active; CI matrix `['3.13']` only (line 21), standalone job uses `3.13` (line 184 active); `PROJECT.md:97` active 3.13 line with 3.10 commented out at line 96 |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py` | Concurrent index swap: adds partial index, removes old full index | VERIFIED | Exists; `atomic = False`; uses `SafeAddIndexConcurrently` and `SafeRemoveIndexConcurrently` (subclasses of Django's concurrent operations); depends on `0027_*`; index DDL is `['queue_name', '-priority', 'created_at'] WHERE status='queued'` name `sqlery_job_pending_idx` |
| `src/sqlery/django_sqlery/models.py` | Updated `Meta.indexes` with partial index | VERIFIED | Old unnamed index at line 592 commented out per project convention; new named partial index at lines 593-597 with correct fields and `Q(status="queued")` condition |
| `src/sqlery/django_sqlery/backend.py` | Keyset-batched `cleanup_jobs` | VERIFIED | `CLEANUP_BATCH_SIZE = 500` at line 27; `FINISHED_STATUSES` at line 28; `import time` at line 9; batched loop at lines 497-514; old unbounded `.delete()` commented out at lines 484-488 |
| `src/sqlery/fastapi_sqlery/backend.py` | Keyset-batched `cleanup_jobs` | VERIFIED | `CLEANUP_BATCH_SIZE = 500` at line 25; `FINISHED_STATUSES` at line 26; `import time` at line 9; batched loop at lines 739-766; old unbounded `session.exec(stmt)` commented out at lines 717-720 |
| `tests/test_batched_cleanup.py` | Behavioral tests for batched cleanup invariants | VERIFIED | Exists with 4 tests: `test_cleanup_never_deletes_claimed_job`, `test_cleanup_issues_multiple_batches_not_one`, `test_cleanup_dry_run_does_not_delete`, `test_cleanup_batch_sleep_is_called` |
| `tests/test_partial_index_12_01.py` | Structural tests for partial index and migration 0028 | VERIFIED | Exists with `TestPartialPendingIndex` (4 test methods) and `TestMigration0028` (4 test methods) |
| `pyproject.toml` | `requires-python = ">=3.13"`; 3.10/3.11/3.12 classifiers commented out | VERIFIED | tomllib parse confirms `requires-python = ">=3.13"` active; only 3.13 and 3.14 version classifiers active; old lines commented not deleted |
| `.github/workflows/test.yml` | CI matrix `['3.13']`; standalone job on 3.13 | VERIFIED | YAML parse confirms `python-version: ['3.13']` in matrix; standalone job step 2 uses `python-version: '3.13'`; 3.10/3.11/3.12 lines commented out |
| `.planning/PROJECT.md` | Constraints section references 3.13+ | VERIFIED | Line 96 has 3.10 constraint commented out; line 97 has active 3.13+ constraint with rationale |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `QueuedJob.Meta.indexes` | migration `0028_partial_pending_index.py` | Index name `sqlery_job_pending_idx` present in both | WIRED | Both define identical column set `['queue_name', '-priority', 'created_at']` with `Q(status='queued')` |
| `DjangoBackend.cleanup_jobs` batched loop | status re-check preventing mid-loop claimed row deletion | `query.filter(id__in=ids).delete()` where `query` already has `status` filter applied | WIRED | The `query` object at line 471 applies `status` filter; `query.filter(id__in=ids)` restricts by ID and re-evaluates status via the existing queryset filters; no-progress guard at line 507 ensures loop termination |
| `SQLAlchemyBackend.cleanup_jobs` batched loop | status re-check preventing mid-loop claimed row deletion | `batch_stmt` re-applies status/queue/age filters from `id_stmt` | WIRED | id_stmt and batch_stmt share the same filter conditions at lines 730-756; no-progress guard at line 759 ensures loop termination |
| `pyproject.toml requires-python` | `.github/workflows/test.yml` matrix | Both reference 3.13 | WIRED | `requires-python = ">=3.13"` and CI matrix `['3.13']` are aligned |
| Claim path (`get_claimable_jobs`) | partial index (`sqlery_job_pending_idx`) | `status="queued"` filter at `backend.py:884` matches index condition | WIRED | `get_claimable_jobs` at line 884 filters `status="queued"` and `queue_name__in=queues`, then orders by `-priority, created_at` — exactly matching the partial index columns and condition |

### Data-Flow Trace (Level 4)

Not applicable — this phase modifies DDL (index), config files (pyproject.toml, CI matrix), and backend methods (cleanup_jobs). No UI components or data-rendering artifacts were introduced.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `pyproject.toml` carries `>=3.13` and no stale classifiers | `python3 -c "import tomllib; ..."` | `requires-python: >=3.13`, `stale classifiers: []` | PASS |
| CI matrix contains only `['3.13']` | yaml parse | `python-version: ['3.13']`; standalone step uses `3.13` | PASS |
| `CLEANUP_BATCH_SIZE = 500` defined in both backends | `grep` | Lines 27 and 25 in Django/FastAPI backends respectively | PASS |
| Migration 0028 has `atomic = False` and concurrent operations | `python3` content check | `atomic=False present: True`, both AddIndexConcurrently and RemoveIndexConcurrently present | PASS |
| `sqlery_job_pending_idx` in `models.py Meta.indexes` with correct fields and condition | file read | Confirmed at lines 593-597 | PASS |
| Old full composite index commented out (not deleted) | file read | Line 592: `# Old: models.Index(fields=["queue_name", "status", "-priority", "created_at"])` | PASS |
| No unreferenced debt markers (`TBD`, `FIXME`, `XXX`) in modified files | `grep` | No matches across all 8 modified files | PASS |

### Probe Execution

| Probe | Command | Result | Status |
|-------|---------|--------|--------|
| No phase-declared probes | N/A | No `probe-*.sh` files declared | SKIPPED |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| R1 | 12-01-PLAN.md | Partial index on claim path; old full index removed | SATISFIED | `sqlery_job_pending_idx` in `models.py`; migration 0028 with concurrent swap |
| R2 | 12-02-PLAN.md | Batched DELETE cleanup, no unbounded lock, mid-loop claim safety | SATISFIED | CLEANUP_BATCH_SIZE=500, keyset loop with status re-check in both backends |
| R10 (partial) | 12-02-PLAN.md | SQLite path untouched | SATISFIED | SafeAdd/RemoveIndexConcurrently guards; cleanup path unchanged for SQLite |
| R11 | 12-03-PLAN.md | `requires-python = ">=3.13"`; CI matrix updated | SATISFIED | pyproject.toml, .github/workflows/test.yml, PROJECT.md all updated |

### Anti-Patterns Found

None. All modified files are clean:
- No `TBD`, `FIXME`, or `XXX` markers in any of the 8 modified files
- No `return null`, `return []`, `return {}` stubs in implementation files
- Old/replaced code is commented out per project convention, not deleted
- `import time` at module top-level (not inline) in both backends

### Human Verification Required

None. All success criteria are verifiable programmatically.

**One implementation deviation from plan — verified correct:** The PLAN specified the batch DELETE should use an explicit `status__in=FINISHED_STATUSES` re-check. The actual implementation uses `query.filter(id__in=ids).delete()` where `query` already carries the status filter applied at construction time. A comment at line 502 (Django) and line 744 (FastAPI) explicitly documents why the `FINISHED_STATUSES` re-check approach was abandoned: it caused an infinite-loop regression (the divergent filter would re-select non-finished rows forever). The chosen approach — filtering `id__in=ids` against the same `query` object — is strictly correct: any row that transitioned to a non-matching status between SELECT and DELETE will fail the original `status` filter in `query` and be excluded from the batch DELETE. The no-progress guard (`if not deleted_count: break`) additionally ensures termination even if all selected rows changed state mid-loop.

### Gaps Summary

No gaps. All 4 success criteria are satisfied by the codebase:

1. `sqlery_job_pending_idx` exists in `QueuedJob.Meta.indexes` with the correct fields and `Q(status="queued")` condition; old full composite index line is commented out; migration 0028 performs a concurrent swap that is a no-op on SQLite.
2. Both `DjangoBackend.cleanup_jobs` and `SQLAlchemyBackend.cleanup_jobs` use keyset-batched loops capped at 500 rows per DELETE, with status re-check via queryset filter inheritance, 0.1s inter-batch sleep, and no-progress loop termination guard; four behavioral tests confirm correctness.
3. SQLite path is protected by `SafeAddIndexConcurrently`/`SafeRemoveIndexConcurrently` subclasses that no-op on non-PostgreSQL databases; no changes were made to SQLite job processing logic.
4. `pyproject.toml` has `requires-python = ">=3.13"` active (old `>=3.10` line commented out); 3.10/3.11/3.12 classifiers commented out; CI matrix is `['3.13']` only; standalone-no-django job uses Python 3.13; `PROJECT.md` Constraints section reflects 3.13+ floor.

**Pre-existing environment gaps (out of scope, not regressions):** `tests/test_sqlalchemy_async_backend.py` errors on missing optional `aiosqlite` dep; `tests/integration/test_modes.py` daemon E2E hangs identically on the pre-Phase-12 base commit. Neither is a Phase 12 artifact.

---

_Verified: 2026-06-11_
_Verifier: Claude (gsd-verifier)_
