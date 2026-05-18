# FEATURES

What this app currently does.

## User-facing features

- Run background jobs via Python task queue with Django ORM or SQLAlchemy backends
- Workers claim and execute jobs using atomic SELECT FOR UPDATE SKIP LOCKED
- Daemon manages worker pool lifecycle and scheduled task enqueueing
- Dashboard and CLI for job monitoring and management

## Bug fixes

- 2025-05-18: Fix workers unable to claim jobs due to race condition in worker registration. Workers now auto-register on-demand if not found during claim attempt.

## Known gaps (deferred from bites)

- Worker auto-registration could use retry with exponential backoff instead of immediate creation
- Worker registration security: no whitelist validation or shared secret required
