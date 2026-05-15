---
phase: 04-security-cleanup
plan: 05
subsystem: django-admin-api
tags: [security, csrf, django, audit, sec-03]
requires:
  - SEC-03 requirement
  - Django >= 4.2 (CsrfViewMiddleware in MIDDLEWARE)
provides:
  - CSRF protection on 10 state-changing admin API endpoints
  - Regression test asserting 403 on POST without X-CSRFToken
affects:
  - src/sqlery/django_sqlery/api_views.py
  - tests/test_csrf_regression.py
tech-stack:
  added: []
  patterns:
    - Django CsrfViewMiddleware (session-cookie + token pairing)
    - Client(enforce_csrf_checks=True) test pattern
key-files:
  created:
    - tests/test_csrf_regression.py
  modified:
    - src/sqlery/django_sqlery/api_views.py
decisions:
  - "Removed orphan `csrf_exempt` import from api_views.py since zero usages remained"
  - "RESEARCH listed 11 sites but live grep found 10 — proceeded with the 10 actually present (delta documented)"
  - "Did NOT touch 3 intentional exemptions in views.py (internal_worker, health_check, trigger_view) — token-auth or read-only"
metrics:
  duration: ~8 minutes
  completed: 2026-05-15
---

# Phase 04 Plan 05: SEC-03 CSRF Audit Summary

Removed `@csrf_exempt` from 10 state-changing admin POST endpoints in `src/sqlery/django_sqlery/api_views.py` and added a regression test asserting CSRF middleware now returns 403 for POSTs lacking `X-CSRFToken`.

## Tasks Completed

| # | Task | Commit |
|---|------|--------|
| 1 | Drop `@csrf_exempt` from api_views.py + remove orphan import | `e0eab99` |
| 2 | Add `tests/test_csrf_regression.py` (2 tests, both green) | `9ca12cc` |

## Verification

- `grep -c '@csrf_exempt' src/sqlery/django_sqlery/api_views.py` → **0** ✓
- `grep -c '@csrf_exempt' src/sqlery/django_sqlery/views.py` → **3** ✓
- `uv run pytest tests/test_csrf_regression.py -v` → **2 passed** ✓
- Reversion smoke test (temp restore old api_views.py): `test_csrf_enforced_on_api_clear_jobs` **FAILED** as expected — confirms the test catches regressions.
- `import sqlery.django_sqlery.api_views` succeeds with `DJANGO_SETTINGS_MODULE=tests.settings`.

## Endpoints Protected (10 total)

All in `src/sqlery/django_sqlery/api_views.py`:

1. `api_task_action` (was line 220)
2. `api_stop_job` (was line 292)
3. `api_worker_action` (was line 386)
4. `api_remove_queued_job` (was line 464)
5. `api_enqueue_job_now` (was line 486)
6. `api_job_priority` (was line 618)
7. `api_clear_jobs` (was line 818)
8. `api_archive_scheduled_jobs` (was line 851)
9. `api_vacuum` (was line 889)
10. `api_manual_intervention` (was line 912)

All retain their original `@require_POST` / `@staff_required_json` decorator stack — only the outer `@csrf_exempt` was stripped.

## Endpoints Intentionally Left Exempt (in `views.py`, untouched)

| Function | Reason |
|----------|--------|
| `internal_worker` (line 344) | HMAC-protected via `X-Signature` + `X-Timestamp`; no cookie reliance |
| `health_check` (line 435) | Read-only liveness probe for kubelet/ALB — no auth, no CSRF |
| `trigger_view` (line 928) | Envelope HMAC via `core.triggers.handle`; no cookie reliance |

## Deviations from Plan

### [Rule 1 — Audit delta] RESEARCH listed 11 endpoints, live grep found 10

- **Found during:** Task 1 audit step
- **Issue:** Plan/RESEARCH stated 11 `@csrf_exempt` occurrences in `api_views.py`, with line numbers for 10 explicit hits + an 11th "verify via grep". Fresh `grep -n '@csrf_exempt' src/sqlery/django_sqlery/api_views.py` returned exactly 10 hits — the 11th hypothesized hit does not exist.
- **Fix:** Proceeded with the 10 actual occurrences (all matched the listed line numbers). All `@csrf_exempt` decorators in `api_views.py` are now gone — same end state.
- **Commit:** `e0eab99`

### [Rule 2 — Cleanup] Orphan import removed

- **Found during:** Task 1
- **Issue:** With zero `@csrf_exempt` usages remaining, the `from django.views.decorators.csrf import csrf_exempt` import on line 17 became dead code (and would trip ruff F401).
- **Fix:** Removed the import line.
- **Commit:** `e0eab99`

## Dashboard JS CSRF Audit (plan-checker W4)

Audited `src/sqlery/django_sqlery/static/sqlery/js/dashboard.js` for fetch/XHR calls. Result: **all POSTs already send `X-CSRFToken`**. No JS changes required.

POST callsites verified (all use `getCookie('csrftoken')` or `getCsrfToken()`):

- Line 225 (intervene), 1110, 1147, 1167, 1212, 1232, 1550 (taskAction), 1588 (clearJobs), 1616 (vacuum), 1641, 1787 — all include `X-CSRFToken` header.

GET callsites (no CSRF needed): lines 74, 625, 964, 1361, 1480.

No regression in the dashboard UI is expected after this plan lands.

## Follow-ups

- None. The 3 intentional `@csrf_exempt` exemptions in `views.py` will be formally documented in `docs/SECURITY.md` as part of plan 04-06 (per the CONTEXT plan).

## Known Stubs

None.

## Self-Check: PASSED

- `src/sqlery/django_sqlery/api_views.py`: FOUND (modified, 0 `@csrf_exempt`)
- `tests/test_csrf_regression.py`: FOUND (75 lines, 2 tests passing)
- Commit `e0eab99`: FOUND
- Commit `9ca12cc`: FOUND
- `src/sqlery/django_sqlery/views.py`: UNCHANGED (3 `@csrf_exempt` preserved)
- `pyproject.toml`: UNCHANGED
