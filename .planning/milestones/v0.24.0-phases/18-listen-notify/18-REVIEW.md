---
phase: 18-listen-notify
reviewed: 2026-06-12T00:00:00Z
depth: deep
files_reviewed: 7
files_reviewed_list:
  - src/sqlery/core/pg_notify.py
  - src/sqlery/core/worker.py
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/fastapi_sqlery/backend.py
  - src/sqlery/django_sqlery/settings.py
  - src/sqlery/fastapi_sqlery/config.py
  - tests/test_listen_notify.py
findings:
  critical: 1
  warning: 2
  info: 3
  total: 6
status: issues_found
---

# Phase 18: listen-notify — Code Review Report

**Reviewed:** 2026-06-12
**Depth:** deep
**Files Reviewed:** 7
**Status:** issues_found

## Summary

Reviewed the Phase 18 LISTEN/NOTIFY opt-in dispatch layer. Fork safety for the Django mode is sound: `_close_listen_conn` is registered as a `register_pre_fork` hook on `ForkSafeExecutor` and `_open_listen_conn` as `register_post_fork_parent`; both are registered *before* the first `_fork_ctx.fork()` call; `daemon.py`'s double-fork daemonize path creates `WorkerProcess` only after forking, so `_listen_conn` cannot exist there. The flag-off (SQLite / default) code path is byte-identical to the commented-out original. Channel-name sanitization uses `re.sub([^a-zA-Z0-9_])` + `psycopg.sql.Identifier` for LISTEN — no injection risk.

One **blocker** was found: the SQLAlchemy/standalone `pg_notify` call executes inside a SQLAlchemy 2 implicit autobegin transaction that `session.close()` rolls back, causing the notification to be silently suppressed by PostgreSQL. The NOTIFY feature is completely non-functional in standalone mode. Two warnings: a connection leak in the error path of `_open_listen_conn`, and a channel-name collision risk from 63-char truncation when queue names exceed 52 characters.

---

## Critical Issues

### CR-01: `notify_queue_sqlalchemy` fires in a rolled-back transaction — NOTIFY never dispatched in standalone mode

**File:** `src/sqlery/core/pg_notify.py:119-122` (called from `src/sqlery/fastapi_sqlery/backend.py:348`)

**Issue:** After `session.commit()` on line 336 of `fastapi_sqlery/backend.py`, SQLAlchemy 2's autobegin semantics start a **new implicit transaction** on the very next `session.execute()` call. `notify_queue_sqlalchemy` calls `session.execute(text("SELECT pg_notify(:ch, '')"), ...)` inside that implicit transaction. The `with get_session() as session:` block then exits, and `get_session()`'s `finally: session.close()` rolls back the implicit transaction before it is ever committed. PostgreSQL only dispatches a NOTIFY when the calling transaction **commits**; a rollback suppresses it entirely.

Verified empirically with SQLAlchemy 2.0.44 (the installed version): `session.execute()` after `session.commit()` sets `session.in_transaction() = True`; `session.close()` fires exactly one `rollback` engine event and zero `commit` events. An `INSERT` made in that position is discarded. A `SELECT pg_notify()` in the same position is therefore silently suppressed.

Result: **in standalone/SQLAlchemy mode, pg_notify never reaches any listening worker**. SC1 (sub-100 ms latency) cannot be satisfied for the standalone adapter. The unit tests in `test_pg_notify.py` only verify that `session.execute()` is called — they do not test that the transaction is committed, so the bug is invisible to the test suite.

**Fix:** Emit the notify in a separate AUTOCOMMIT connection rather than through the Session, so there is no transaction to roll back. Replace the `session.execute()` path in `notify_queue_sqlalchemy` with:

```python
# Old: fires in a rolled-back implicit SA2 transaction — notification suppressed
# try:
#     session.execute(_sa_text("SELECT pg_notify(:ch, '')"), {"ch": channel})
# except Exception:
#     ...

# New: use an autocommit connection so the notify fires immediately
try:
    from sqlery.fastapi_sqlery.database import get_engine  # already a top-level dep
    engine = get_engine()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as raw_conn:
        raw_conn.execute(_sa_text("SELECT pg_notify(:ch, '')"), {"ch": channel})
except Exception:
    logger.warning("pg_notify fire failed for channel %r", channel, exc_info=True)
```

Alternatively (if `get_engine` should not be imported into `pg_notify.py`), accept the engine or a raw connection as the second argument rather than a Session.

---

## Warnings

### WR-01: `_open_listen_conn` leaks the psycopg connection when a LISTEN command fails

**File:** `src/sqlery/core/worker.py:888,901-906`

**Issue:** `_psycopg.connect(dsn, autocommit=True)` is assigned to `self._listen_conn` at line 888. The subsequent `for queue in self.queues` loop calls `self._listen_conn.execute(LISTEN ...)` for each queue. If this execute raises (e.g., permission denied on a channel, server closed the connection mid-setup), the outer `except Exception` block at line 901 runs:

```python
except Exception as e:
    logger.warning(...)
    self._listen_conn = None   # ← connection from line 888 is orphaned, never .close()'d
```

The open psycopg connection is never closed. It will remain open on the server until the GC finalises the object (non-deterministic) or until the server-side idle timeout fires.

**Fix:**

```python
except Exception as e:
    logger.warning(
        f"Worker {self.worker_id}: failed to open LISTEN connection: {e}; "
        f"falling back to polling"
    )
    # Close orphaned connection before clearing the reference
    if self._listen_conn is not None:
        try:
            self._listen_conn.close()
        except Exception:
            pass
    self._listen_conn = None
```

---

### WR-02: 63-char truncation in `sanitize_queue_name_to_channel` can silently collapse two distinct queues to the same channel

**File:** `src/sqlery/core/pg_notify.py:49-50`

**Issue:** The prefix `"sqlery_job_"` is 11 characters. After prepending, any queue name longer than 52 characters is truncated to produce a 63-char channel. Two queues that share the same first 52 characters but differ only in the tail map to an **identical** channel name:

```
queue "a"*52 + "X"  →  channel "sqlery_job_" + "a"*52   (63 chars)
queue "a"*52 + "Y"  →  channel "sqlery_job_" + "a"*52   (63 chars, same)
```

Workers listening on either queue receive NOTIFY wake-ups intended for the other. This does not cause data corruption (claiming is still atomic/correct) but causes spurious wake-ups on wrong queues, potentially wasted claim attempts, and slightly elevated DB load. More critically, workers for the short-name queue never get the wakeup if only the long-name queue was notified and no worker is listening on the collapsed channel.

The collision only affects queue names longer than 52 characters, which is unusual in practice, but the silent failure mode (cross-queue wakeups with no error) makes it a robustness concern.

**Fix:** Truncate only the *sanitized queue suffix* to `63 - len("sqlery_job_")` = 52 characters *before* prepending the prefix, then assert the full channel is ≤ 63 chars:

```python
# Old: truncates after prefixing (prefix absorbs chars, silent collision possible)
# channel = f"sqlery_job_{sanitized}"
# return channel[:63]

# New: truncate the sanitized portion first, then prepend
PREFIX = "sqlery_job_"
max_suffix = 63 - len(PREFIX)  # 52
sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", queue_name)[:max_suffix]
channel = f"{PREFIX}{sanitized}"
# channel is guaranteed ≤ 63 chars; truncation is visible in the suffix, not the prefix
return channel
```

This does not prevent collision for extremely long names that share a 52-char prefix, but it makes the truncation *visible* (the suffix is cut, not the prefix) and avoids producing channels where the prefix itself is mangled.

---

## Info

### IN-01: Inline `from django.db import connection` imports inside `pg_notify.py` functions violate project convention

**File:** `src/sqlery/core/pg_notify.py:63, 83`

**Issue:** `_fire_django_notify` (line 63) and `notify_queue_django` (line 83) each contain a `from django.db import connection` import inside the function body. The project CLAUDE.md convention (and the global `~/.claude/CLAUDE.md`) requires all imports at the top of the file (99.99% of cases). Both functions suppress the ruff `PLC0415` warning with `# noqa`, acknowledging the violation but not resolving it.

The module-level `try: from django.db import transaction as _django_transaction` pattern already guards against missing Django. The same pattern could guard `from django.db import connection as _django_connection` at the top level.

**Fix:**

```python
# At module level, alongside the existing _django_transaction guard:
try:
    from django.db import connection as _django_connection
except ImportError:
    _django_connection = None  # type: ignore[assignment]
```

Then replace `from django.db import connection` in both function bodies with `_django_connection`, removing the `noqa` comments.

---

### IN-02: Latency acceptance test uses raw f-string for LISTEN SQL instead of `psycopg.sql.Identifier`

**File:** `tests/test_listen_notify.py:314-317`

**Issue:** The test opens a LISTEN connection with:

```python
listen_conn.execute(f"LISTEN {channel}")
```

Production code (`worker.py:892-895`) uses `psycopg.sql.SQL("LISTEN {}").format(psycopg.sql.Identifier(channel))`. The raw f-string is safe here because `channel` is the output of `sanitize_queue_name_to_channel` (which guarantees `[a-zA-Z0-9_]+`), but the inconsistency means the test exercises a different code path than production and could silently pass even if the `Identifier` quoting were broken in production.

**Fix:** Replace the test's LISTEN statement with the same Identifier-quoted form used in production:

```python
import psycopg.sql as _sql
listen_conn.execute(
    _sql.SQL("LISTEN {}").format(_sql.Identifier(channel))
)
```

---

### IN-03: No test coverage for SQLAlchemy backend notify path in `test_listen_notify.py`

**File:** `tests/test_listen_notify.py` (missing test), `src/sqlery/fastapi_sqlery/backend.py:338-348`

**Issue:** `test_listen_notify.py` contains only `DjangoBackend`-based tests for the enqueue notify path (SC2 flag-off, SQLite no-op). The `SQLAlchemyBackend.create_job` notify integration (lines 343-348 of `fastapi_sqlery/backend.py`) has no corresponding acceptance test in this file. The existing `tests/unit/test_pg_notify.py` tests only `notify_queue_sqlalchemy` in isolation with a mock session — they do not test the `SQLAlchemyBackend.create_job` → `notify_queue_sqlalchemy` integration. CR-01 above (the rolled-back transaction bug) was invisible precisely because no integration test exercised this path end-to-end.

**Fix:** Add a `TestFlagOffBehavior` equivalent for `SQLAlchemyBackend` in `test_listen_notify.py` asserting that `_notify_queue_sqlalchemy` is not called when `SQLERY_PG_NOTIFY=False`, mirroring the Django test at line 85. Also add a flag-on integration test (PG-gated) that verifies a real NOTIFY is dispatched to a listener after `SQLAlchemyBackend.create_job`.

---

_Reviewed: 2026-06-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
