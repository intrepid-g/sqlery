## 2025-05-18 — Fix worker claim race condition

**Built:** Auto-register workers on-demand when claiming jobs if worker row not found in database, preventing "workers idle but jobs waiting" scenario.

**Decisions:**
- Race condition handling: chose immediate auto-registration, alternative was retry loop with exponential backoff
- Worker validation: chose trust-on-first-claim (any worker ID format is accepted), alternative was whitelist/shared secret validation

**Deferred:**
- Add retry loop with exponential backoff before auto-registration
- Add worker registration security (whitelist or shared secret)

## 2026-05-21 — Dialect-aware atomic claiming in standalone backend

**Built:** Standalone sync backend now detects PostgreSQL vs SQLite and uses the correct atomic claiming strategy: SELECT FOR UPDATE SKIP LOCKED for Postgres, optimistic version-based CAS update for SQLite. Prevents duplicate job execution when multiple workers run against SQLite.

**Decisions:**
- Claim strategy detection: chose hardcoded dialect name check (`postgresql` → skip_locked, `sqlite` → optimistic_version), alternative was backend configuration flag or capability table
- SQLite CAS path: chose inline UPDATE with version check inside the same session, alternative was a separate "claim" stored procedure or advisory lock emulation

**Deferred:**
- Multi-worker PostgreSQL concurrent claim stress test under real contention (PG-only, needs CI service)

## Bite queue

1. Add retry loop with exponential backoff before auto-registration `[pending]`
2. Add worker registration security (whitelist or shared secret) `[pending]`
3. Multi-worker PostgreSQL concurrent claim stress test under real contention `[pending]`

## Ignored bites

(None yet)
