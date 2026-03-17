# Sqlery - Project Review

## ✅ Works Perfectly

- Core queue + cron scheduling
- Atomic job claiming (no duplicate execution)
- Atomic scheduler claiming (no duplicate enqueueing)
- Retry logic with exponential backoff
- Job arguments + @job decorator
- Admin interface + multiple deployment modes
- Subprocess trigger mode (production-ready, no HTTP complexity)
- Absolute path resolution (works in all deployments)
- Comprehensive test coverage

## 🔧 Needs TLC

### Critical (Production Blockers):
- ~~**Fragile subprocess**~~ - ✅ **FIXED (v0.6.2)** - now uses absolute path from settings.BASE_DIR
- **httpx import crash** - imported at module level, fails without httpx even when not using HTTP mode
- **Database bloat** - unlimited output/error/traceback field sizes

### High Priority (Annoyances):
- **Stale schedules** - editing cron expression doesn't recalculate next_run_at
- **Rigid concurrency** - failed job retries block new scheduled runs

### Nice to Have:
- Separate scheduler/worker throttle intervals
- Better cache sharing across processes
- Stricter signature timeout tolerance
