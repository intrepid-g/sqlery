# Quick task: Worker auto-registration retry loop

## Context
When a worker tries to claim a job and its worker row is not found in the database, it currently auto-registers immediately. A retry loop with exponential backoff before creating the row would be more robust against transient visibility delays.

## Goal
Add a configurable retry loop (with exponential backoff and max retries) in `DjangoBackend._resolve_worker` / `_auto_register_worker` before falling back to creating the worker row.

## Entry points
- `src/sqlery/django_sqlery/backend.py` — `_resolve_worker`, `_auto_register_worker`
- `src/sqlery/django_sqlery/settings.py` — add setting for retry count / backoff

## Acceptance
- Unit test shows worker registration retries before creating row
- Default behavior remains immediate creation (backward compatible)
