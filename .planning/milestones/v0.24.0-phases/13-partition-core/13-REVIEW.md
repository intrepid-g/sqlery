---
phase: 13-partition-core
reviewed: 2026-06-11T00:00:00Z
depth: deep
files_reviewed: 4
files_reviewed_list:
  - src/sqlery/core/partitioning.py
  - src/sqlery/core/cleanup.py
  - src/sqlery/core/daemon.py
  - tests/unit/test_partitioning.py
findings:
  critical: 1
  warning: 3
  info: 3
  total: 7
status: issues_found
---

# Phase 13: Code Review Report

**Reviewed:** 2026-06-11
**Depth:** deep
**Files Reviewed:** 4
**Status:** issues_found

## Summary

Reviewed the three production files added/changed in Phase 13 (partition-core) plus the companion unit-test suite. SQL identifier safety — the top-stated concern — is sound: every DDL statement in `partitioning.py` uses `psycopg.sql.Identifier` or parameterized `%s` placeholders; there is no raw f-string interpolation of table or partition names anywhere.

One blocker was found: the unconditional module-level import of `psycopg` (via `partitioning.py`) now crashes `daemon.py` on import in SQLite-only environments where `psycopg` is not installed. Three warnings address correctness gaps in the reclaim path (DETACH without DROP in some autocommit edge cases), partition naming for sub-daily intervals, and an overly broad exception catch that masks connection errors as attach-conflict. Three info items cover duplication, a hardcoded name prefix, and a test gap.

---

## Critical Issues

### CR-01: `daemon.py` crashes on import when `psycopg` is not installed (SQLite-only environments)

**File:** `src/sqlery/core/daemon.py:18`
**Issue:** `daemon.py` unconditionally imports `partitioning` at module level:

```python
from . import partitioning as _partitioning
```

`partitioning.py` unconditionally imports `psycopg` at module level (lines 19–20). `psycopg` is an *optional* dependency (`[project.optional-dependencies] postgres = ["psycopg>=3.1"]`), not in the base `dependencies` list. Any SQLite-only install that starts the daemon (or imports `daemon.py` for any reason) gets `ImportError: No module named 'psycopg'`. The project explicitly supports SQLite as a first-class deployment target across all execution modes (CLAUDE.md, project constraints).

**Fix:** Guard the import with `try/except ImportError` and set `_partitioning` to `None` when psycopg is absent. Gate the call site (already inside a `try` block) on `_partitioning is not None`:

```python
# daemon.py — replace the bare import at line 18
try:
    from . import partitioning as _partitioning
except ImportError:
    _partitioning = None  # psycopg not installed; partition maintenance unavailable
```

Then in `_run_daemon`, gate on both the enabled flag and availability:

```python
# before the maintenance tick:
_partition_maint_available = _partitioning is not None
if partition_maintenance_enabled and _partition_maint_available and _should_run_partition_maintenance(...):
    ...
```

---

## Warnings

### WR-01: Partition name collision for sub-daily `SQLERY_PARTITION_INTERVAL` values

**File:** `src/sqlery/core/partitioning.py:158`
**Issue:** The partition name is derived from `lo.strftime("%Y%m%d")` — only the date, not the time:

```python
name = "sqlery_queued_job_" + lo.strftime("%Y%m%d")
```

When `SQLERY_PARTITION_INTERVAL` is set to anything shorter than one day (e.g. `"1 hour"` or `"6 hours"`), multiple iterations produce the same name (e.g. `sqlery_queued_job_20250101` for every hour on 2025-01-01). PostgreSQL's `CREATE TABLE IF NOT EXISTS` means only the *first* iteration's range gets a partition; all subsequent same-name iterations are no-ops. The partitions for hours 1–23 are silently never created. There is no error; the caller receives an inflated `created` count (actually `1` instead of `24`).

D1 locks the default to `"1 day"`, but `interval_str` is a free-form config parameter. An operator who follows the `"1 hour"` sketch mentioned in the context doc's superseded section would get silent incorrect behavior.

**Fix:** Derive the name from the *full* `lo` timestamp, truncated to the precision of the interval. Simplest correct approach:

```python
# replace line 158
name = partition_table + "_" + lo.strftime("%Y%m%d_%H%M")
```

Or conditionally based on interval granularity. At minimum, add a docstring warning and log a startup error when the validator detects a sub-daily interval.

---

### WR-02: `archive_hook` DB error in non-autocommit mode can prevent `DROP TABLE` and leave a dangling detached partition

**File:** `src/sqlery/core/partitioning.py:264–276`
**Issue:** After `DETACH PARTITION` succeeds, the `archive_hook` is called with the *same cursor* (same underlying connection). If the hook issues a failing database statement (any `psycopg.DatabaseError`), the connection enters an aborted-transaction state. The outer `except Exception` in lines 267–272 catches the Python exception, but in a non-autocommit connection the transaction is now aborted. The subsequent `DROP TABLE` on line 274 then fails with `"ERROR: current transaction is aborted"` (the exception propagates up uncaught, exits the partition loop early, and `dropped` is not incremented).

In *autocommit* mode, `DETACH` is already committed but `DROP` fails separately — the table is permanently detached but never dropped. Either way the result is a dangling standalone table that the next reclaim cycle cannot find (because `_list_partitions` only queries `pg_inherits`, which no longer includes the detached table).

The `reclaim_drained_partitions` docstring says "autocommit or inside a transaction — caller's choice" but does not document this hazard.

**Fix:** Require the cursor to use autocommit (document the constraint, or assert `cur.connection.autocommit`), and wrap `DROP TABLE` in its own `try/except` to protect the advisory-unlock `finally` from also failing:

```python
try:
    cur.execute(
        pgsql.SQL("DROP TABLE {name}").format(name=pgsql.Identifier(name))
    )
    dropped += 1
except psycopg.DatabaseError as drop_exc:
    logger.error(
        "DROP TABLE failed for detached partition %s: %s — manual cleanup required",
        name, drop_exc,
    )
```

---

### WR-03: `ensure_future_partitions` catches all `psycopg.DatabaseError` including connection failures, masking them as attach-conflicts

**File:** `src/sqlery/core/partitioning.py:172–185`
**Issue:** The inner exception handler uses:

```python
except Exception as exc:
    if not isinstance(exc, psycopg.DatabaseError):
        raise
    # ... treat as attach-conflict, continue
```

This catches *every* `psycopg.DatabaseError` subclass, including `psycopg.OperationalError` (connection lost), `psycopg.InterfaceError`, and `psycopg.errors.DiskFull`. These are connection-level or server-level failures that should not be silently swallowed and continued past. The loop `continue`s to the next iteration, issuing more DB statements on a likely-broken connection, producing a chain of caught errors. The advisory-unlock `finally` may also fail silently on a broken connection (though the session-level lock is released when the connection closes, so no permanent lock leak exists).

The intent was clearly to catch only `InvalidTableDefinition` / `ExclusionViolation` (attach-conflict). The comment says so, but the code doesn't restrict to those.

**Fix:** Narrow the catch to the specific error classes that indicate an attach-conflict:

```python
except (
    psycopg.errors.InvalidTableDefinition,
    psycopg.errors.CheckViolation,
    psycopg.errors.ExclusionViolation,
) as exc:
    logger.warning(
        "Partition attach conflict for %s: %s "
        "— rows in DEFAULT partition, manual cleanup required",
        name, exc,
    )
    continue
```

---

## Info

### IN-01: `check_default_partition` duplicates the `_list_partitions` catalog query

**File:** `src/sqlery/core/partitioning.py:301–315`
**Issue:** `check_default_partition` re-executes the same `pg_inherits + pg_get_expr` SQL that `_list_partitions` already encapsulates, scanning for the DEFAULT partition inline. When `check_default_partition` is always called in the daemon after `reclaim_drained_partitions` (which already called `_list_partitions`), this is a third round-trip to the same catalog tables.

**Fix:** Refactor to call `_list_partitions` and filter for the `None`-bound entry:

```python
def check_default_partition(cur, table: str) -> int:
    default_name = next(
        (name for name, upper in _list_partitions(cur, table) if upper is None),
        None,
    )
    if default_name is None:
        return 0
    # ... COUNT(*) query unchanged
```

---

### IN-02: Partition name hardcodes `"sqlery_queued_job_"` prefix rather than deriving from the `table` parameter

**File:** `src/sqlery/core/partitioning.py:158`
**Issue:** The `ensure_future_partitions` function accepts `table: str` as a parameter but hardcodes the partition name prefix:

```python
name = "sqlery_queued_job_" + lo.strftime("%Y%m%d")
```

If `table` is ever anything other than `"sqlery_queued_job"` (future tenant isolation, renamed table), partitions will be created with the wrong name but correctly attached to the actual table. PostgreSQL does not require partition names to reference their parent, so this silently succeeds but leaves confusing catalog state. Today the daemon hardcodes `partition_table = "sqlery_queued_job"`, so there is no active bug — but the API contract (the parameter) and the implementation are inconsistent.

**Fix:**

```python
name = table + "_" + lo.strftime("%Y%m%d")
```

---

### IN-03: `test_advisory_lock_released_after_reclaim` only exercises the empty-partition path; DROP failure path not tested

**File:** `tests/unit/test_partitioning.py:508–514`
**Issue:** The test passes `parts=[]`, so the loop body never executes and the `finally` block is trivially reached. There is no test that verifies `pg_advisory_unlock` still executes when `DROP TABLE` raises mid-loop (equivalent of `test_advisory_lock_released_even_on_error` for `ensure_future_partitions`). This leaves the most operationally dangerous path — a DROP failure that bypasses the advisory unlock and the remaining partitions — untested.

**Fix:** Add a test mirroring `TestEnsureFuturePartitions.test_advisory_lock_released_even_on_error`:

```python
def test_advisory_lock_released_even_if_drop_raises(self):
    """Advisory lock must be released even if DROP TABLE raises."""
    from sqlery.core.partitioning import reclaim_drained_partitions
    old_upper = _utcnow() - timedelta(days=60)
    cur = self._make_reclaim_cursor(
        lock_acquired=True,
        parts=[("sqlery_queued_job_old", old_upper)],
        live_work_results=[False],
    )
    def side_effect(sql, params=None):
        if "DROP" in str(sql).upper():
            raise RuntimeError("disk full")
    cur.execute.side_effect = side_effect
    with pytest.raises(RuntimeError):
        reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
    all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
    assert "pg_advisory_unlock" in all_sqls
```

---

_Reviewed: 2026-06-11_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
