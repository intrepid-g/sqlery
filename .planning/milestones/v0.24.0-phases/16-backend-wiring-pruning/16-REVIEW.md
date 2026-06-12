---
phase: 16-backend-wiring-pruning
reviewed: 2026-06-12T00:00:00Z
depth: deep
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/django_sqlery/async_backend.py
  - src/sqlery/django_sqlery/db_compat.py
  - src/sqlery/django_sqlery/models.py
  - src/sqlery/core/daemon.py
  - src/sqlery/django_sqlery/migrations/0031_secondary_indexes.py
findings:
  critical: 3
  warning: 3
  info: 2
  total: 8
status: issues_found
---

# Phase 16: Code Review Report

**Reviewed:** 2026-06-12
**Depth:** deep
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 16 wires `_partitioned_pg()` / `get_raw_cursor()` into the backend and adds `created_at` to every write-path filter (11-item checklist). The CAS additions are **sound**: `created_at` comes from the DB in every case (either from the model instance read at claim time or from an explicit `values("created_at")` SELECT), Django's ORM round-trips `DateTimeField` at full microsecond precision, and every filter retains `version` where required by D7. No silent-miss risk identified in the CAS paths.

Three blockers exist in the daemon/cursor integration layer.

---

## Critical Issues

### CR-01: Daemon maintenance block passes `None` cursor to partitioning functions on non-partitioned PG

**File:** `src/sqlery/core/daemon.py:618-635`

**Issue:** The maintenance block at line 618 is gated on `_partition_maint_available` (psycopg installed) but NOT on whether the table is actually partitioned. On a PostgreSQL install where `migration 0030` has not yet run, `backend.get_raw_cursor()` returns `None` (because `_partitioned_pg()` returns `False`). The cursor is then passed directly to `_partitioning.ensure_future_partitions(None, ...)`, `reclaim_drained_partitions(None, ...)`, etc., each of which immediately calls `cur.execute(...)` and raises `AttributeError: 'NoneType' object has no attribute 'execute'`. The outer `except Exception` at line 685 catches and logs the error, so the daemon continues, but:
- Partition maintenance silently fails on every tick for the life of the daemon.
- `last_partition_maintenance_at` is never updated, so the daemon retries every `partition_maintenance_interval` minutes and log-spams indefinitely.

```python
# daemon.py line 624 — add null check:
cur = backend.get_raw_cursor()
if cur is None:
    # Non-partitioned PG or SQLite — skip maintenance tick silently.
    last_partition_maintenance_at = datetime.now(timezone.utc)
else:
    made = _partitioning.ensure_future_partitions(...)
    ...
```

---

### CR-02: `cleanup_jobs` leaks the raw cursor (never closed)

**File:** `src/sqlery/django_sqlery/backend.py:618`

**Issue:** `cur = self.get_raw_cursor()` returns `connection.cursor()`, a Django `CursorWrapper`. This cursor is passed to `reclaim_drained_partitions` and then the function returns immediately — the cursor is never closed. Under the Django psycopg backend, this leaves the underlying psycopg cursor open until garbage collection. On every `cleanup_jobs` call from the daemon's periodic loop this creates a new unclosed cursor, slowly exhausting the cursor pool on busy systems.

```python
# Use a context manager so the cursor always closes:
with self.get_raw_cursor() as cur:
    dropped = _partitioning.reclaim_drained_partitions(
        cur, self.QueuedJob._meta.db_table, retention_str, archive_hook
    )
```

Note: `get_raw_cursor()` would also need to be updated to return a context-manager-compatible object, or the call site can call `cur.close()` in a `finally` block.

---

### CR-03: Promotion tick passes `None` cursor to `promote_due_scheduled_jobs` on non-partitioned PG

**File:** `src/sqlery/core/daemon.py:697-706`

**Issue:** The promotion block at line 693 is gated on `_partition_maint_available` (psycopg installed) but not on `_partitioned_pg()`. On non-partitioned PG, `backend.get_raw_cursor()` returns `None`, which is then passed to `promote_due_scheduled_jobs(None)`. That function calls `cur.execute("SELECT pg_try_advisory_lock(%s)", ...)` at line 444 of `scheduler.py`, raising `AttributeError`. The comment at line 704-705 says this is caught "intentionally" as a Phase 16 TODO, but Phase 16 has now wired `get_raw_cursor()`, so the `AttributeError` guard is masking a real bug: promotion never runs on non-partitioned PG even when it should run (the staging table still exists).

The `AttributeError` catch at line 704 is now a bug-hiding pattern rather than a graceful degradation. A null cursor should produce a clear error or skip cleanly via an explicit null check:

```python
cur = backend.get_raw_cursor()
if cur is None:
    pass  # Non-partitioned PG/SQLite — promotion not available
else:
    try:
        promoted = promote_due_scheduled_jobs(cur)
        ...
    except Exception as promo_exc:
        logger.error(f"Promotion tick error: {promo_exc}", exc_info=True)
```

---

## Warnings

### WR-01: `_partitioned_pg_cache` permanently set to `False` on any transient DB error

**File:** `src/sqlery/django_sqlery/backend.py:71-84`

**Issue:** On any exception during the `pg_class` catalog query, `_partitioned_pg_cache` is set to `False` and cached permanently (line 83). A transient connection error at startup (e.g., DB briefly unavailable while the connection pool warms up) causes the backend to treat the table as non-partitioned for the lifetime of the process. This silently disables all partition-based routing — write-path pruning, reclaim routing, vacuum skip — with no retry and no log warning distinguishing "non-partitioned table" from "DB error at startup."

**Fix:** On exception, leave `_partitioned_pg_cache = None` so the next call retries, and log at WARNING level:

```python
except Exception:
    # Fail open: leave cache as None so the next call retries.
    logger.warning(
        "_partitioned_pg: catalog query failed — will retry on next call",
        exc_info=True,
    )
    return False
```

---

### WR-02: `DjangoAsyncBackend._partitioned_pg()` issues a DB roundtrip on every call (no cache)

**File:** `src/sqlery/django_sqlery/async_backend.py:54-73`

**Issue:** `DjangoBackend._partitioned_pg()` caches its result in `_partitioned_pg_cache` after the first query. The async counterpart at line 54 queries `pg_class` on every invocation with no caching. Currently `_partitioned_pg()` is only called from `acleanup_jobs`, which is infrequent, but the inconsistency violates the parity goal stated in the docstring and will become a hotspot if the method is called from additional paths.

**Fix:** Add an instance-level cache field `_partitioned_pg_cache: bool | None = None` in `__init__` (if not already present) and mirror the caching logic from `DjangoBackend`.

---

### WR-03: `cascade_ancestor_status` (item 9) UPDATE has no status guard — can overwrite terminal-status ancestors

**File:** `src/sqlery/django_sqlery/backend.py:912-914`

**Issue:** The UPDATE at line 912 is `filter(id=current_id, created_at=...)` with no `status` guard. If a parent job has already reached a terminal status (`success`, `archived`) from a previous run, this call overwrites it with whatever `status` is passed in. The old code had the same gap, so this is a pre-existing issue — not introduced by Phase 16 — but the two-step SELECT makes the absence of a status guard more visible. The CONTEXT says "the CAS filters gain `created_at`, never lose `version`," and `cascade_ancestor_status` has no `version` field in its filter either (also pre-existing). Adding a status guard is low-risk and correct:

```python
# Only update non-terminal ancestors.
self.QueuedJob.objects.filter(
    id=current_id,
    created_at=job_row["created_at"],
).exclude(
    status__in=("success", "archived")
).update(status=status)
```

---

## Info

### IN-01: Stale TODO comment in daemon.py promotion tick

**File:** `src/sqlery/core/daemon.py:695`

**Issue:** The comment at line 695 says `"TODO(Phase 16): backend.get_raw_cursor() is wired in Phase 16."` Phase 16 has now been executed and `get_raw_cursor()` is wired. The TODO is stale and the `except AttributeError` at line 704 with its comment `"backend.get_raw_cursor() not yet wired — Phase 16 TODO"` should be removed or replaced with the explicit null-check guard described in CR-03.

**Fix:** Remove the TODO comment and replace `except AttributeError` with the explicit null check.

---

### IN-02: Migration 0031 index names differ from `Meta.indexes` names — silent divergence

**File:** `src/sqlery/django_sqlery/migrations/0031_secondary_indexes.py:26-47`

**Issue:** The comment at line 29-33 explains that the Meta index names cannot be reused because `sqlery_queued_job_legacy` still holds them. This is correct. However, the consequence is that Django's migration state (`state_operations=[]`) will never include these physical indexes in its tracking. If a future `makemigrations` run creates a "logical" index with the same Meta name, it will create a *third* physical index under the Meta name without dropping either the legacy-table copy or the 0031 copy. The comment calls this out, but it should be tracked as a maintenance debt.

**Fix:** Add a `# TODO(post-partition-drop-migration)` comment and a note in `REGRESSIONS.md` to recreate these indexes under their canonical Meta names once `sqlery_queued_job_legacy` is dropped and the name conflicts are resolved.

---

## CAS Correctness Verdict (D7 Focus)

All 11 checklist items were traced:

- **Items 1–2 (db_compat.py CAS):** `job.created_at` is present on full model instances returned by `get_claimable_jobs()`. `version` is retained. Filter is `id + created_at + status + version`. **Sound.**
- **Items 3–5 (models.py mark_*):** `self.created_at` is from `auto_now_add` (set at INSERT) or from a prior `refresh_from_db()`. `version` is retained. **Sound.**
- **Item 6 (save_meta):** Already wired in Phase 15. **Sound.**
- **Items 7–8 (cancel_job, mark_job_archived):** Two-step SELECT + UPDATE. Status guard is re-applied in the UPDATE filter. Between SELECT and UPDATE a job could transition status, causing `updated=0` — this is a pre-existing TOCTOU risk not introduced by Phase 16, and the status guard in the UPDATE makes it safe (won't corrupt a running job). **Sound.**
- **Item 9 (cascade_ancestor_status):** `created_at` added, no `version` field (pre-existing, not on the checklist). Status guard absent (see WR-03). **Sound for D7 semantics; WR-03 applies.**
- **Item 10 (get_job_by_id):** Full-row SELECT, no `.only()` trimming `created_at`. Marked verified in code comments. **Sound.**
- **Item 11 (update_job_child_pid):** Optional `created_at` parameter; id-only fallback documented. **Sound per D7 "degrades gracefully" acceptance.**

`created_at` timezone/microsecond round-trip: Django `DateTimeField` round-trips through the ORM at full microsecond precision. The value used in every CAS filter originates from a DB read (either `auto_now_add`, `refresh_from_db`, or an explicit `values("created_at")` SELECT), so there is no mismatch between the in-hand value and the stored value. **No silent-miss risk from timestamp precision.**

---

_Reviewed: 2026-06-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
