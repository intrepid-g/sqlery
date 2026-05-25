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

## 2026-05-25 — Fork-safe connection lifecycle

**Built:** Replaced manual `_reset_db_connections()` discipline in the fork path with `ForkSafeExecutor` — a hook-based system that guarantees DB connections are closed before `os.fork()` and reopened in both parent and child, with leak verification.

**Decisions:**
- Hook resolution: chose string identifiers resolved by `auto_configure()` (`build_default_hooks()`), alternative was direct callable registration — forced by: pure functions must be testable without Django/SQLAlchemy imports; revisit when: hook set grows beyond Django+SQLAlchemy
- Leak verification: chose log-and-warn on leaked connections (`verify_no_open_connections()`), alternative was raise-on-leak (hard failure) — forced by: existing code has error-recovery `_reset_db_connections()` calls that may leave intentional transient connections; revisit when: all error-recovery paths migrated to hooks

**Deferred:**
- Migrate remaining error-recovery `_reset_db_connections()` calls (lines 196, 559, 674, 748) to ForkSafeExecutor hooks
- Support user-registered custom pre/post-fork hooks (e.g. for connection pools, file descriptors, shared memory)

## Bite queue

1. Migrate error-recovery `_reset_db_connections()` calls to ForkSafeExecutor hooks `[pending]`
2. Add retry loop with exponential backoff before auto-registration `[pending]`
3. Add worker registration security (whitelist or shared secret) `[pending]`
4. Multi-worker PostgreSQL concurrent claim stress test under real contention `[pending]`
5. Support user-registered custom pre/post-fork hooks `[pending]`

## Ignored bites

(None yet)
