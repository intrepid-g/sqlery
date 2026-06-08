# Deferred Items — Phase 08

## Pre-existing migration chain bug (out of scope for 08-01)

**Discovered during:** Plan 08-01, Task 2 verification (`alembic upgrade head` on a fresh SQLite DB).

**Issue:** `alembic upgrade head` from an empty database fails at revision `20250101_0002_worker_table.py` with:
`sqlite3.OperationalError: table sqlery_worker already exists`.

**Root cause:** The initial migration `20250101_0001_initial_schema.py` already creates the `sqlery_worker` table (lines 95-110), and the subsequent migration `20250101_0002_worker_table.py` creates it again. The collision predates Phase 08 entirely (these are `20250101` migrations) and also occurs when upgrading only to the prior head `20260514_0014` — i.e. it is **not** caused by the new `20260608_0015` migration.

**Why deferred:** Scope boundary — this plan only adds the lease table at the END of the chain. Fixing the `0001`/`0002` duplication is unrelated migration-history surgery that would touch files outside this plan's `files_modified`.

**Verification used instead for 08-01:** The new migration was verified in isolation by stamping a fresh DB at `20260514_0014`, then `upgrade head` → `downgrade 20260514_0014`. This confirmed: single linear head resolves to `20260608_0015`, `down_revision == '20260514_0014'`, the `sqlery_daemon_lease` table is created with the exact 7 columns / `queue_name` PK / `ix_sqlery_daemon_lease_expires_at` index / `version` server_default `'0'`, and downgrade drops the index then the table. `SQLModel.metadata.create_all` also produces the table.

**Suggested fix (future):** Make `20250101_0002` a no-op (or have `0001` not create `sqlery_worker`) so the chain is replayable end-to-end from empty. Track separately from Phase 08 lease parity work.
