---
phase: 10-harden-cron-semantics
plan: 02
subsystem: config
tags: [config, cron, jitter, CRON-03, scheduler]
requires: []
provides:
  - "config key: scheduler_jitter_seconds (standalone, default 0, float env-overridable)"
  - "config key: SCHEDULER_JITTER_SECONDS (Django DEFAULTS, default 0)"
affects:
  - "Plan 03 scheduler (reads jitter via get_config)"
tech-stack:
  added: []
  patterns:
    - "Config default-dict knob in both StandaloneConfig._config and Django DEFAULTS"
    - "Env-var float parsing in StandaloneConfig._load_from_env"
key-files:
  created: []
  modified:
    - src/sqlery/fastapi_sqlery/config.py
    - src/sqlery/django_sqlery/settings.py
decisions:
  - "Default 0 = jitter off in both modes (PROJECT.md locked)"
  - "Standalone literal key is lowercase scheduler_jitter_seconds; Django DEFAULTS key is UPPER_SNAKE SCHEDULER_JITTER_SECONDS (matches each mode's convention)"
  - "Did NOT change get_config/get_setting/DjangoConfig — key resolves to default 0 in both modes via get_config's default arg; no behavior change needed"
metrics:
  duration: ~7m
  completed: 2026-06-08
---

# Phase 10 Plan 02: Scheduler Jitter Config Surface Summary

Added the optional `scheduler_jitter_seconds` jitter knob (default `0`, jitter off) to both config default sets so Plan 03's scheduler can read it via `get_config` in Django and standalone modes; standalone is env-overridable as a float.

## What Was Built

- **Standalone (`src/sqlery/fastapi_sqlery/config.py`):** Added `'scheduler_jitter_seconds': 0` to `StandaloneConfig._config` under the Daemon/scheduler settings block. Added `'SQLERY_SCHEDULER_JITTER_SECONDS': 'scheduler_jitter_seconds'` to `env_mappings` and a `float(env_value)` conversion branch so the env override parses as a float. No new imports (`os` already top-level).
- **Django (`src/sqlery/django_sqlery/settings.py`):** Added `"SCHEDULER_JITTER_SECONDS": 0` to the `DEFAULTS` dict under the Scheduler settings section, matching the existing UPPER_SNAKE_CASE convention.

## CRITICAL — Cross-Mode Key Resolution for Plan 03 (REQUIRED READING)

I traced `get_config` (`src/sqlery/compat/__init__.py:904-923`) end-to-end. **`get_config` performs NO key transformation** — it passes the literal key straight to `config.get(key, default)`. The two Config adapters resolve differently:

- **Standalone — `StandaloneConfig.get` (`config.py:114-116`):** `self._config.get(key, default)`. The literal key is the lowercase dict key `scheduler_jitter_seconds`.
- **Django — `DjangoConfig.get` (`django_sqlery/config.py:27-29`):** `getattr(settings, 'DJANGO_SQL_JOBS', {}).get(key, default)`. **This reads `DJANGO_SQL_JOBS` ONLY — it does NOT consult `DEFAULTS` and does NOT call `get_setting`.** So the Django `DEFAULTS['SCHEDULER_JITTER_SECONDS']` entry is documentation/discoverability for operators who set `DJANGO_SQL_JOBS`, and the self-healing path; it is NOT what `get_config` falls back to.

### Exact literal key string(s) Plan 03 must pass

The key strings are **NOT identical across modes** (lowercase standalone vs UPPER_SNAKE Django). Both, however, resolve to the supplied default `0` when the operator has not overridden them, because `get_config(key, 0)` returns its own `default` arg when the key is absent. Plan 03 has two correct options:

1. **Recommended (mode-aware, matches each mode's stored key so operator overrides actually take effect):**
   ```python
   from sqlery.compat import is_django_mode  # or however mode is detected in scheduler
   jitter_key = "SCHEDULER_JITTER_SECONDS" if <django mode> else "scheduler_jitter_seconds"
   jitter = get_config(jitter_key, 0)
   ```
   Verify the actual mode-detection helper available in `core/scheduler.py` (the scheduler already imports from compat). If a clean mode flag is not readily available, option 2 is acceptable for the default-only path but will silently ignore operator overrides in one mode.

2. **Simpler (single literal, but operator overrides only honored in the matching mode):** call `get_config('scheduler_jitter_seconds', 0)` — returns `0` (default) in BOTH modes when unset; in standalone it also honors `SQLERY_SCHEDULER_JITTER_SECONDS`; in Django a user-set `DJANGO_SQL_JOBS['scheduler_jitter_seconds']` (lowercase) would be honored, but the documented Django key is the UPPER_SNAKE one. To honor the Django UPPER_SNAKE override, use option 1.

**`get_setting` is case-sensitive and exact-match** (`settings.py:200-243`) — it does NOT upper-case or normalize keys. There is no case-insensitivity to rely on.

**Recommendation for Plan 03:** use the mode-aware lookup (option 1) so operator overrides work in both modes, OR — if a one-liner is strongly preferred — have the scheduler read both keys with the same default and take the first non-default (e.g. `get_config('SCHEDULER_JITTER_SECONDS', 0) or get_config('scheduler_jitter_seconds', 0)`), treating `0`/None as disabled. Whichever is chosen, Plan 03 must BOUND the value via `random.uniform(0, jitter)` and treat non-positive/None as disabled (threat T-10-04).

## Verification

- Task 1 verify printed `STANDALONE JITTER OK`: `StandaloneConfig().get('scheduler_jitter_seconds') == 0`; `SQLERY_SCHEDULER_JITTER_SECONDS=2.5` yields `2.5` as a `float`.
- `grep -n "scheduler_jitter_seconds" src/sqlery/fastapi_sqlery/config.py` returns 4 hits (default entry + env mapping + float branch + comment).
- Task 2 verify printed `DJANGO DEFAULT OK; get_setting resolves: 0`: `DEFAULTS['SCHEDULER_JITTER_SECONDS'] == 0`; `get_setting('SCHEDULER_JITTER_SECONDS', 0) == 0`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `black --check` cannot pass on either file — pre-existing, out of scope**
- **Found during:** Verification (the plan's `<verification>` requires `black --check` to pass on both files).
- **Issue:** Both files FAIL `black --check` in their pristine pre-existing state (confirmed by running `black --check` against `HEAD:` versions). `config.py` uses single quotes throughout (black wants double quotes); `settings.py` has blank-line spacing inside the `DEFAULTS` dict that black strips. These failures are NOT caused by this plan's changes.
- **Fix/Decision:** Did NOT run `black` to reformat. Reformatting would touch every line of both pre-existing files (every quote in `config.py`, every dict blank line in `settings.py`), which (a) is out of scope per the executor scope boundary (only auto-fix issues directly caused by the current task) and (b) conflicts with the user's global rule against blanket line replacement. My added lines are themselves black-clean: the `config.py` additions follow the file's existing single-quote convention for local consistency; the `settings.py` additions use double quotes and conform to black. The `black --check` verification step therefore reports the pre-existing failure, not a regression from this plan.
- **Files modified:** none beyond the planned additions.
- **Commit:** n/a (no reformatting performed).

**2. [Documentation] Cross-mode key resolution finding**
- The plan's Task 2 asked to trace and document whether `get_config` resolves the key in both modes and whether a mapping change is required. Trace result documented in the "CRITICAL — Cross-Mode Key Resolution" section above. No `get_config`/`get_setting`/`DjangoConfig` behavior change was required — the key resolves to the supplied default `0` in both modes — so none was made, per the plan's instruction not to change resolution behavior unless the trace proves the key cannot resolve.

## Threat Surface

T-10-04 (covert delay channel): this plan only defines the default `0` (jitter off) and introduces no delay. The value is operator-set config (settings/env), not request-derived. Bounding the applied delay (`random.uniform(0, jitter)`, non-positive treated as disabled) is Plan 03's responsibility and is reiterated above. No new packages added (T-10-SC: config-dict edits only).

## Self-Check: PASSED

- `src/sqlery/fastapi_sqlery/config.py` — FOUND (modified, committed 4e8ab79)
- `src/sqlery/django_sqlery/settings.py` — FOUND (modified, committed 5d249af)
- Commit 4e8ab79 — FOUND
- Commit 5d249af — FOUND
