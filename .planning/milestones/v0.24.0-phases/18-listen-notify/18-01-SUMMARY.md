---
phase: 18-listen-notify
plan: "01"
subsystem: config
tags: [config, feature-flag, pg-listen-notify, django, standalone]

dependency_graph:
  requires: []
  provides:
    - SQLERY_PG_NOTIFY flag in Django DEFAULTS (settings.py)
    - SQLERY_PG_NOTIFY flag in StandaloneConfig (config.py)
  affects:
    - src/sqlery/django_sqlery/backend.py (future consumer via get_setting)
    - src/sqlery/fastapi_sqlery/backend.py (future consumer via get_config)

tech_stack:
  added: []
  patterns:
    - Boolean env-var loading (lower() in ('true','1','yes')) — consistent with ENABLE_DAEMON pattern

key_files:
  created: []
  modified:
    - src/sqlery/django_sqlery/settings.py
    - src/sqlery/fastapi_sqlery/config.py

decisions:
  - "SQLERY_PG_NOTIFY defaults to False (opt-in) — byte-identical polling when unset (D1/D8)"
  - "StandaloneConfig env-var parsing follows existing ENABLE_DAEMON boolean pattern"
  - "Flag added after INTERNAL_ALLOWED_IPS block in both files to mirror structure"

metrics:
  duration: "~8 minutes"
  completed: "2026-06-12T15:52:08Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 2
---

# Phase 18 Plan 01: Feature Flag (SQLERY_PG_NOTIFY) Summary

**One-liner:** Added `SQLERY_PG_NOTIFY=False` opt-in flag to both config systems (Django DEFAULTS + StandaloneConfig) with env-var loading, defaulting to off (byte-identical polling behaviour).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add SQLERY_PG_NOTIFY to Django DEFAULTS | c0e76de | src/sqlery/django_sqlery/settings.py |
| 2 | Add SQLERY_PG_NOTIFY to StandaloneConfig | 26b026f | src/sqlery/fastapi_sqlery/config.py |

## Changes Made

### Task 1 — Django DEFAULTS (settings.py)

Added `"SQLERY_PG_NOTIFY": False` to the `DEFAULTS` dict immediately after the DB resilience block (`PG_LOCK_TIMEOUT_MS`). The existing `get_setting()` fallback chain reads from `DJANGO_SQL_JOBS` first then `DEFAULTS`, so the new key is automatically accessible via `get_setting('SQLERY_PG_NOTIFY', False)` with no changes to `get_setting`.

### Task 2 — StandaloneConfig (config.py)

Two additions:

1. `'SQLERY_PG_NOTIFY': False` in `self._config` dict, after the `INTERNAL_ALLOWED_IPS` entry.
2. In `_load_from_env`: a standalone bool-parse block reading `os.getenv("SQLERY_PG_NOTIFY")`, coercing via `lower() in ("true", "1", "yes")` — consistent with the existing `ENABLE_DAEMON` boolean pattern. Partition-related env vars and `_validate_partition_config` are untouched.

## Verification

```
DEFAULTS['SQLERY_PG_NOTIFY'] == False                             PASS
StandaloneConfig().get('SQLERY_PG_NOTIFY') == False               PASS
SQLERY_PG_NOTIFY=true  → StandaloneConfig().get(...) == True      PASS
SQLERY_PG_NOTIFY=1     → StandaloneConfig().get(...) == True      PASS
SQLERY_PG_NOTIFY=false → StandaloneConfig().get(...) == False     PASS
tests/test_core_standalone.py  5 passed, 2 skipped                PASS
tests/ -k "config or setting"  28 passed, 4 skipped               PASS
```

One pre-existing intermittent test (`test_cron_fires_exactly_once_under_threaded_overlap`) logs a SQLite "database table is locked" race under threaded concurrency when the full suite runs in parallel. This failure predates this plan and is unrelated to config changes — it passes when run in isolation.

## Deviations from Plan

None — plan executed exactly as written. Both edits are purely additive; no existing lines were deleted or replaced.

## Known Stubs

None — flag is fully wired in both config systems. Consumers (Phase 18 plans 02+) will read it via `get_setting`/`get_config`.

## Threat Flags

None — the boolean env-var parse (string comparison to known literals) introduces no new injection surface. Arbitrary env content maps to True or False only.

## Self-Check: PASSED

- [x] `src/sqlery/django_sqlery/settings.py` modified — `SQLERY_PG_NOTIFY` in DEFAULTS
- [x] `src/sqlery/fastapi_sqlery/config.py` modified — `SQLERY_PG_NOTIFY` in `_config` + env load
- [x] Commit c0e76de exists (Task 1)
- [x] Commit 26b026f exists (Task 2)
- [x] All config/settings tests pass; no new failures introduced
