---
phase: 16-backend-wiring-pruning
fixed_at: 2026-06-12T00:00:00Z
review_path: .planning/phases/16-backend-wiring-pruning/16-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 16: Code Review Fix Report

**Fixed at:** 2026-06-12
**Source review:** .planning/phases/16-backend-wiring-pruning/16-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5 (CR-01, CR-02, CR-03, WR-01, WR-02, WR-03 — IN findings excluded by default scope)
- Fixed: 5
- Skipped: 0

## Fixed Issues

### CR-01 + CR-03 + IN-01: Daemon passes None cursor to partitioning / promotion functions

**Files modified:** `src/sqlery/core/daemon.py`
**Commit:** `bd29634`
**Applied fix:** In the partition maintenance tick, added an explicit `if cur is None` guard after `backend.get_raw_cursor()`. When the cursor is None (non-partitioned PG or SQLite), the tick advances `last_partition_maintenance_at` and logs at DEBUG level to prevent busy-looping, then skips all partitioning calls. In the promotion tick, replaced the stale `except AttributeError` guard (which was masking a real bug — promotion silently never ran) with an explicit `if cur is None: pass` block. Removed the Phase-16 TODO comment (IN-01) and the `except AttributeError` with its "not yet wired" comment, replacing both with clean null-check pattern.

---

### CR-02: cleanup_jobs leaks the raw cursor

**Files modified:** `src/sqlery/django_sqlery/backend.py`
**Commit:** `7ca268c`
**Applied fix:** Wrapped `reclaim_drained_partitions` in a `try/finally` block that calls `cur.close()` when `cur is not None`. The cursor is now guaranteed to be closed after `reclaim_drained_partitions` returns, even if the function raises. Old unclosed assignment commented out per CLAUDE.md convention.

---

### WR-01: _partitioned_pg caches False on transient DB error

**Files modified:** `src/sqlery/django_sqlery/backend.py`
**Commit:** `7feb3eb`
**Applied fix:** In the `except Exception` block of `DjangoBackend._partitioned_pg`, removed the `self._partitioned_pg_cache = False` assignment. Instead, log at WARNING with `exc_info=True` and `return False` without updating the cache. This leaves `_partitioned_pg_cache` as None so the next call retries the catalog query. A startup transient DB error no longer permanently disables partition routing for the process lifetime.

---

### WR-02: DjangoAsyncBackend._partitioned_pg re-queries on every call

**Files modified:** `src/sqlery/django_sqlery/async_backend.py`
**Commit:** `9d8c1f5`
**Applied fix:** Added `__init__` to `DjangoAsyncBackend` with `self._partitioned_pg_cache: bool | None = None`. Updated `_partitioned_pg` to check and set the cache before the catalog query (mirroring `DjangoBackend`). Applied the same WR-01 retry pattern: on exception, leave cache as None and return False. Non-PostgreSQL vendors are cached as False immediately (same as sync backend).

---

### WR-03: cascade_ancestor_status can overwrite terminal-status ancestors

**Files modified:** `src/sqlery/django_sqlery/backend.py`
**Commit:** `8141412`
**Applied fix:** Added `.exclude(status__in=("success", "archived"))` to the UPDATE queryset in `cascade_ancestor_status`. Terminal ancestors are now skipped cleanly — an UPDATE that would have overwritten a completed or archived parent now hits 0 rows instead. The `created_at` partition-prune filter and the existing `parent_job_id` walk logic are both preserved unchanged.

---

## Test Results

**SQLite unit suite (496 tests, from main project dir with worktree source):**
```
496 passed, 26 skipped, 3 xfailed in 3.83s
```
No new failures.

**PG tests (test_pruning_explain, test_lifecycle_partitioned, test_divergence_matrix):**
```
32 passed, 3 skipped in 1.78s
```
All pass.

---

_Fixed: 2026-06-12_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
