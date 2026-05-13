# Phase 02 - Deferred Items

Items observed during plan execution that are out of scope for the current
plan but should be addressed by a future plan (per the SCOPE BOUNDARY rule).

## From plan 02-07

### D-02-07-1: Pre-existing pytest-django migration bug — `table "sqlery_daemon_lease" already exists`

**Discovered during:** 02-07 Task 2 verification (`pytest tests/integration/test_modes.py`).

**Symptom:** Any pytest run that triggers Django's `setup_databases` (i.e.
fixtures `db`, `transactional_db`) on an in-memory SQLite errors with:

```
django.db.utils.OperationalError: table "sqlery_daemon_lease" already exists
```

The same error reproduces on a fresh `manage.py migrate sqlery` against a
brand-new SQLite file. It is **not** caused by plan 02-07's changes.

**Reproduce:** at base commit 81500a27, run
`PYTHONPATH=. uv run pytest tests/test_models.py::TestScheduledTask::test_scheduled_task_creation`.
The error fires before any 02-07 code is loaded.

**Likely cause:** Duplicate `CreateModel("DaemonLease")` between two Django
migrations in `src/sqlery/django_sqlery/migrations/`. Needs a `migrations`
audit to find the dup.

**Impact on 02-07:** The 6 SQLite-backed E2E cells in
`tests/integration/test_modes.py` cannot complete under pytest until this
bug is fixed. The harness itself is functionally correct; verified by
driving `(sync, django, sqlite)` end-to-end outside pytest's
`setup_databases` flow:

```
enqueued id=1 status: queued
final status: success result: 3
PASS sync-django-sqlite
```

**Suggested fix owner:** Phase 4 hardening, OR a dedicated `02-07.5`
follow-up. Should NOT be folded into a planned 02-08 task because 02-08
will hit the same blocker and cannot proceed without it either.
