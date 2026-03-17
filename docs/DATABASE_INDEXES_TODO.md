# Database Indexes TODO (SQ-36, SQ-37)

**Priority**: High (marked with [!] in ISSUE_TRACKER.md)
**Estimated Time**: 1-2 hours
**Impact**: 10-100x performance improvement on hot paths

---

## Critical Indexes Needed

### 1. Job Claiming Hot Path
**Current**: Full table scan on every claim operation
**Query Pattern**: `WHERE status = 'queued' AND queue_name IN (...) ORDER BY priority DESC, created_at ASC`

```sql
-- Composite index for optimal job claiming
CREATE INDEX idx_jobs_claiming
ON sqlery_queued_job(status, queue_name, priority DESC, created_at ASC);

-- Alternative: Separate indexes if composite too large
CREATE INDEX idx_jobs_status ON sqlery_queued_job(status);
CREATE INDEX idx_jobs_queue ON sqlery_queued_job(queue_name);
CREATE INDEX idx_jobs_priority ON sqlery_queued_job(priority DESC);
```

**Files to Modify**:
- `src/sqlery/backends/sync_backend.py` - Add index creation in schema setup
- `src/sqlery/backends/async_backend.py` - Add index creation in schema setup
- `alembic/versions/` - Create migration for existing installations

---

### 2. Scheduled Jobs
**Query Pattern**: `WHERE scheduled_at <= NOW() AND status = 'queued'`

```sql
CREATE INDEX idx_jobs_scheduled
ON sqlery_queued_job(scheduled_at, status)
WHERE scheduled_at IS NOT NULL;
```

---

### 3. Worker Queries
**Query Pattern**: `WHERE worker_id = '...'`

```sql
CREATE INDEX idx_jobs_worker
ON sqlery_queued_job(worker_id)
WHERE worker_id IS NOT NULL;
```

---

### 4. Cleanup/Reporting Queries
**Query Pattern**: `WHERE created_at < ... AND status IN (...)`

```sql
CREATE INDEX idx_jobs_cleanup
ON sqlery_queued_job(created_at, status);
```

---

### 5. Registry Queries
**Query Pattern**: `WHERE job_id = ... AND registry_type = ...`

```sql
CREATE INDEX idx_registry_lookup
ON sqlery_registry(job_id, registry_type);

CREATE INDEX idx_registry_type
ON sqlery_registry(registry_type, entered_at DESC);
```

---

## Implementation Strategy

### Phase 1: Add to Schema Creation (New Installations)
- Update table creation SQL in both backends
- Add indexes during initial table setup
- Zero downtime for new installs

### Phase 2: Migration for Existing Installations
- Create Alembic migration
- Add `CREATE INDEX IF NOT EXISTS` for safety
- Support both PostgreSQL and SQLite syntax

### Phase 3: Verification
- Run EXPLAIN ANALYZE on critical queries
- Benchmark before/after
- Document performance improvements

---

## PostgreSQL vs SQLite Differences

### PostgreSQL
- Supports partial indexes: `WHERE scheduled_at IS NOT NULL`
- Supports `CREATE INDEX CONCURRENTLY` for zero-downtime
- Better query planner, can use multiple indexes

### SQLite
- No concurrent index creation (but fast enough)
- Limited partial index support (check version)
- Simpler query planner, benefits from composite indexes

**Strategy**: Create dialect-specific index SQL, similar to job claiming logic

---

## Performance Impact Estimate

| Operation | Before | After | Improvement |
|-----------|--------|-------|-------------|
| Job Claiming | O(n) full scan | O(log n) index seek | **10-100x** |
| Scheduled Job Check | O(n) full scan | O(log n) + filter | **50-500x** |
| Worker Job Lookup | O(n) full scan | O(1) index | **100x+** |
| Cleanup Query | O(n) full scan | O(log n) range | **10-50x** |

**With 10,000 jobs**: ~10ms → ~0.1ms per claim operation

---

## Files to Modify

```
src/sqlery/backends/
├── sync_backend.py          - Add _create_indexes() method
├── async_backend.py         - Add _create_indexes() method
└── base.py                  - Document index requirements

alembic/versions/
└── YYYYMMDD_HHMM_add_performance_indexes.py  - Migration

tests/backends/
├── test_sync_backend.py     - Add index verification tests
└── test_async_backend.py    - Add index verification tests

ISSUE_TRACKER.md             - Mark SQ-36, SQ-37 as complete
```

---

## Testing Strategy

1. **Unit Tests**: Verify indexes exist after table creation
2. **Performance Tests**: Benchmark claim_job() before/after
3. **Integration Tests**: Verify indexes work on both PostgreSQL and SQLite
4. **Migration Tests**: Verify migration runs successfully on existing databases

---

## Related Issues

- **SQ-36**: Optimize job claiming query (primary beneficiary)
- **SQ-37**: Add database indexes for common queries (this TODO)
- **SQ-35**: Benchmark Queue API performance (should do after indexes)
- **SQ-67**: SQLite tag locking performance (separate optimization)

---

**Next Steps When Ready**:
1. Create `_create_indexes()` method in both backends
2. Add index creation to table setup
3. Create Alembic migration
4. Add verification tests
5. Run benchmarks and document improvements
6. Update ISSUE_TRACKER.md

**Status**: ✅ COMPLETED (2025-01-25)

## Implementation Summary

All indexes have been implemented in `src/sqlery/schema.py`:

- **11 common indexes** that work on both SQLite and PostgreSQL
- **4 PostgreSQL-specific partial indexes** for additional optimization
- **Automatic index creation** when calling `create_tables_sync()` or `create_tables_async()`
- **Standalone functions** `create_indexes_sync()` and `create_indexes_async()` for existing databases
- **Registry table** also added to schema (was previously missing)
- **16 comprehensive tests** in `tests/test_schema_indexes.py`

See `src/sqlery/schema.py` for the full implementation.
