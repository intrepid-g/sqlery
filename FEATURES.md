# FEATURES

What this app currently does.

## User-facing features

- Run background jobs via Python task queue with Django ORM or SQLAlchemy backends
- Workers claim and execute jobs using atomic SELECT FOR UPDATE SKIP LOCKED on PostgreSQL
- Workers claim and execute jobs using optimistic version-based locking on SQLite (prevents duplicate execution under concurrent workers)
- Daemon manages worker pool lifecycle and scheduled task enqueueing
- Dashboard and CLI for job monitoring and management

## Bug fixes

- 2025-05-18: Fix workers unable to claim jobs due to race condition in worker registration. Workers now auto-register on-demand if not found during claim attempt.
- 2026-05-21: Fix standalone sync backend unconditionally using SELECT FOR UPDATE SKIP LOCKED on SQLite, which does not support row-level locking and caused race conditions where multiple workers could claim the same job. Now uses optimistic version-CAS updates on SQLite while keeping SKIP LOCKED on PostgreSQL.

## Known gaps (deferred from bites)

- Worker auto-registration could use retry with exponential backoff instead of immediate creation
- Worker registration security: no whitelist validation or shared secret required
- Multi-worker PostgreSQL concurrent claim stress test under real contention (PG-only, needs CI service)
