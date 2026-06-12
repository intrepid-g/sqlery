---
phase: 12-quick-wins
reviewed: 2026-06-11T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/django_sqlery/models.py
  - src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/fastapi_sqlery/backend.py
  - pyproject.toml
  - .github/workflows/test.yml
findings:
  critical: 1
  warning: 2
  info: 2
  total: 5
status: findings
---

# Phase 12: Code Review Report

**Reviewed:** 2026-06-11
**Depth:** standard
**Files Reviewed:** 6
**Status:** findings

## Summary

Reviewed the six files changed in Phase 12 (partial pending index, keyset-batched cleanup, Python 3.13 floor). The infinite-loop regression fixed in eed941e is correctly resolved. The Django backend's batched loop is sound. The SQLAlchemy backend has one critical schema-drift issue on SQLite and one dead-code warning. One warning exists in the SQLAlchemy backend around a stale variable. Two info-level items round out the findings.

---

## Critical Issues

### CR-01: SQLite schema diverges from ORM state after migration 0028

**File:** `src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py:19-51`

**Issue:** `SafeAddIndexConcurrently.database_forwards` and `SafeRemoveIndexConcurrently.database_forwards` both return early (`return`) on non-PostgreSQL vendors. On SQLite this means:
- The new partial index `sqlery_job_pending_idx` is **never created** in the SQLite database.
- The old composite index `sqlery_queu_queue_n_5c87d6_idx` is **never dropped** from the SQLite database.

Django's migration framework marks the migration as applied regardless of what `database_forwards` does, so after `manage.py migrate` on SQLite the migration state says the swap occurred but the actual DB schema still holds the old index and lacks the new one. This causes `manage.py migrate --check` to report no problems while the schema is wrong, and it also means the `models.py` Meta.indexes definition (which now lists only `sqlery_job_pending_idx`) is permanently inconsistent with the SQLite schema. SQLite 3.8.9+ supports partial indexes natively and Django can create them — there is no technical reason to skip the entire operation on SQLite.

**Fix:** Split the operation into two concerns. Use `SafeAdd/RemoveIndexConcurrently` only to strip the `CONCURRENTLY` keyword on SQLite, not to skip entirely. Override `database_forwards` to fall back to a regular (non-concurrent) index operation on SQLite:

```python
class SafeAddIndexConcurrently(AddIndexConcurrently):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            # Fall back to a regular (blocking) index create on non-PG vendors.
            # AddIndex is safe inside the atomic=False migration on SQLite.
            from django.db import migrations as _mig
            _mig.AddIndex(
                model_name=self.model_name,
                index=self.index,
            ).database_forwards(app_label, schema_editor, from_state, to_state)
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            from django.db import migrations as _mig
            _mig.RemoveIndex(
                model_name=self.model_name,
                name=self.index.name,
            ).database_backwards(app_label, schema_editor, from_state, to_state)
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class SafeRemoveIndexConcurrently(RemoveIndexConcurrently):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            from django.db import migrations as _mig
            _mig.RemoveIndex(
                model_name=self.model_name,
                name=self.name,
            ).database_forwards(app_label, schema_editor, from_state, to_state)
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            from django.db import migrations as _mig
            _mig.AddIndex(
                model_name=self.model_name,
                index=self._get_index_from_state(to_state, app_label),
            ).database_backwards(app_label, schema_editor, from_state, to_state)
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)
```

This keeps the concurrent path for PostgreSQL and uses standard blocking DDL for SQLite, aligning the actual DB schema with the ORM state on all vendors.

---

## Warnings

### WR-01: Dead `stmt` variable in SQLAlchemy `cleanup_jobs` non-dry-run path

**File:** `src/sqlery/fastapi_sqlery/backend.py:692-702`

**Issue:** `stmt = delete(QueuedJob)` is built at line 692 and conditionally extended at lines 695, 698, and 702. In the non-dry-run path, this `stmt` is never executed — the actual deletions use `batch_stmt` constructed inside the loop (line 750). The `stmt` variable is leftover from the old unbounded-delete implementation. It wastes three conditional branches on every call and is misleading to future readers.

**Fix:** Comment out the dead `stmt` construction in the non-dry-run path (per project convention — comment out, do not delete):

```python
# Old: stmt built here was used by the now-removed unbounded delete path.
# Kept as reference; batch_stmt inside the loop is the live delete path.
# stmt = delete(QueuedJob)
# if status:
#     stmt = stmt.where(QueuedJob.status == status)
# if queue_name:
#     stmt = stmt.where(QueuedJob.queue_name == queue_name)
# if max_age_days:
#     cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
#     stmt = stmt.where(QueuedJob.created_at < cutoff)
```

### WR-02: `max_count` parameter silently ignored in both backends

**File:** `src/sqlery/django_sqlery/backend.py:463`, `src/sqlery/fastapi_sqlery/backend.py:686`

**Issue:** Both `cleanup_jobs` implementations accept `max_count: int | None = None` (matching the ABC at `src/sqlery/compat/__init__.py:349`) but neither implementation reads it. The ABC docstring describes it as "Maximum number of jobs to delete". No current caller passes it, but the parameter is part of the public contract. A caller passing `max_count=100` would silently delete far more than 100 rows (up to the full matching set, in batches of 500). This is a silent contract violation.

**Fix:** Either implement the cap (stop the batch loop once `total_deleted >= max_count`) or document explicitly that `max_count` is unimplemented and raise `NotImplementedError` if passed a non-None value, so callers get an explicit error rather than silent over-deletion:

```python
# In cleanup_jobs, after the dry_run early return:
if max_count is not None:
    raise NotImplementedError("max_count is not yet implemented in cleanup_jobs; use cleanup_jobs_by_count")
```

---

## Info

### IN-01: `dry_run` path in SQLAlchemy backend duplicates filter logic three times

**File:** `src/sqlery/fastapi_sqlery/backend.py:704-715`

**Issue:** The `dry_run` path builds `count_stmt` by re-applying the same `status`, `queue_name`, and `max_age_days` conditionals that were already applied to the dead `stmt` (lines 694-702) and will be applied again to `id_stmt` (lines 730-736). The same three-branch filter is written three times in the same function. The Django backend avoids this entirely — `dry_run` in the Django path calls `query.count()` on the already-filtered queryset (line 481), requiring zero duplication.

**Fix:** Refactor the SQLAlchemy `dry_run` branch to use `id_stmt` (which is built before the loop regardless) instead of a separate `count_stmt`:

```python
# Build id_stmt early (before the dry_run check) and reuse it for counting:
if dry_run:
    count = session.exec(select(func.count()).select_from(id_stmt.subquery())).one()
    return {"deleted": 0, "count": count}
```

This eliminates the third copy of the filter logic and brings parity with the Django backend's dry_run implementation.

### IN-02: Classifiers in `pyproject.toml` do not include Python 3.14

**File:** `pyproject.toml:28-29`

**Issue:** The `requires-python = ">=3.13"` floor means the package is installable on Python 3.14, but the classifiers list only `"Programming Language :: Python :: 3.13"` and `"Programming Language :: Python :: 3.14"` (line 29 already present — this is fine). No action needed on 3.14. However, the CI matrix (`test.yml:21`) runs only `['3.13']` with no forward-looking `3.14` entry. When 3.14 reaches stable this will need a matrix expansion. Consider adding a comment placeholder.

**Fix:** Add a comment in the CI matrix:

```yaml
python-version: ['3.13']
# python-version: ['3.13', '3.14']  # add 3.14 when stable
```

---

_Reviewed: 2026-06-11_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
