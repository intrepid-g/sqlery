# Plan 16-01 Summary: backend helpers + staging gate + migration 0031

**Status:** Complete (implemented inline by orchestrator after the executor subagent stalled twice with zero recoverable commits).

## What was built

**Commits:**
- `feat(16-01): _partitioned_pg + get_raw_cursor + staging gate` (backend.py)
- `feat(16-01): migration 0031 secondary indexes + async _partitioned_pg` (0031, async_backend.py)

### backend.py (`DjangoBackend`)
- `_partitioned_pg()` — True iff `connection.vendor == 'postgresql'` AND `sqlery_queued_job` has `relkind='p'` (partitioned). Cached per-process. Fails safe to False. This is the helper the Phase-13 cleanup seam and Phase-14 staging gate probe via `hasattr`.
- `get_raw_cursor()` — returns the Django connection's raw cursor on partitioned PG (so the daemon's promotion/reclaim/ensure maintenance actually runs — Phase 16 carry-forward #1); returns `None` on SQLite / non-partitioned PG so the daemon skips PG-only maintenance cleanly.
- `create_job` staging routing now gated on `self._partitioned_pg()` (carry-forward #2): far-future jobs route to `sqlery_scheduled_job` ONLY on partitioned PG. SQLite / non-partitioned PG keep far-future jobs in `sqlery_queued_job` (D6 — SQLite path unchanged; the PG-only promotion loop can't drain a staging table on SQLite anyway).

### async_backend.py (`DjangoAsyncBackend`)
- `_partitioned_pg()` mirror for parity (16-03 mirrors cleanup→reclaim there).

### migration 0031_secondary_indexes.py (carry-forward #3 / Phase 15 IN-01)
- PG-only (vendor-guarded), depends on 0030. `CREATE INDEX IF NOT EXISTS` for the 12 secondary indexes that 0030's `LIKE INCLUDING DEFAULTS` did NOT copy to the partitioned table (4 remaining Meta.indexes + 8 single-column db_index fields). Reverse drops them. `state_operations=[]` (model state already declares them → makemigrations clean).
- **Gotcha handled:** the 4 Meta-named indexes were given distinct `sqlery_qj_*` names because the renamed `sqlery_queued_job_legacy` table still holds the canonical Meta index names (schema-unique), which would silently no-op `CREATE INDEX IF NOT EXISTS`. The planner selects by index shape, not name.

## Verification
- Full migration chain applies on fresh PG incl. 0031: **14 indexes** on the partitioned parent (was 2 after 0030).
- `makemigrations --check` clean.
- SQLite unit suite: 489 passed; the **11 failures are all staging/dual-table/enqueue-routing tests** that assumed SQLite routes far-future jobs to staging — EXPECTED from the gate change, to be updated in plan **16-04**. No unrelated regressions.

## Handoff to 16-04
Update these 11 tests so staging behavior is asserted under partitioned PG (or via a `_partitioned_pg` mock), and SQLite asserts far-future jobs stay in `sqlery_queued_job`.
