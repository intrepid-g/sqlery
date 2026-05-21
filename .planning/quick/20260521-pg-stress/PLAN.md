# Quick task: PostgreSQL concurrent claim stress test

## Context
The standalone sync backend now uses SELECT FOR UPDATE SKIP LOCKED on PostgreSQL, but there is no automated test that verifies two workers cannot claim the same job under real PG contention. The SQLite path has a threading race test; PG needs an equivalent.

## Goal
Add a test (or test class) that starts multiple threads/processes against a real PostgreSQL database and asserts that exactly one worker claims each job.

## Entry points
- `tests/unit/test_sqlalchemy_backend_sync.py` — add PG mirror concurrency test class
- Requires `SQLERY_TEST_PG_URL` env var (already used by existing PG mirror tests)

## Acceptance
- Test passes when `SQLERY_TEST_PG_URL` is set
- Test verifies no duplicate claims under contention
- Test is skipped gracefully when PG URL is absent
