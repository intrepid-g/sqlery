# Quick task: Worker registration security

## Context
Workers currently auto-register with any worker ID format. There is no validation, whitelist, or shared secret required. In multi-tenant or untrusted environments this could allow rogue workers to join the pool.

## Goal
Add optional security to worker registration: either a worker ID whitelist (env var / setting) or a shared secret that workers must present.

## Entry points
- `src/sqlery/django_sqlery/backend.py` — `_auto_register_worker`
- `src/sqlery/django_sqlery/settings.py` — add `WORKER_WHITELIST`, `WORKER_SHARED_SECRET`
- `src/sqlery/core/worker.py` — pass secret/token if configured

## Acceptance
- When security is enabled, unknown worker IDs are rejected
- When disabled (default), behavior is unchanged
- Tests cover both enabled and disabled paths
