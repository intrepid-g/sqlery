---
phase: 04-security-cleanup
verified: 2026-05-15T12:38:44Z
status: passed
score: 13/13 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: null
  previous_score: null
  gaps_closed: []
  gaps_remaining: []
  regressions: []
deferred:
  - truth: "Webhook validation closes the DNS-rebinding window between validation and HTTP send"
    addressed_in: "v2 hardening (out of scope for v1)"
    evidence: "src/sqlery/security/ssrf.py:10-14 and docs/SECURITY.md document the ~50ms DNS-rebinding gap; pinning resolved IP via custom HTTPAdapter explicitly deferred."
  - truth: "Webhook validation re-checks after HTTP redirects"
    addressed_in: "v2 hardening"
    evidence: "04-04-SUMMARY notes redirect re-validation gap; v1 ships with single pre-flight check."
  - truth: "IPv4-mapped IPv6 (::ffff:10.0.0.1) normalization"
    addressed_in: "v2 hardening"
    evidence: "04-04-SUMMARY notes IPv4-mapped IPv6 normalization not implemented; documented limitation."
  - truth: "Celery drop-in compat shim"
    addressed_in: "BACKLOG.md / next milestone"
    evidence: ".planning/BACKLOG.md routes celery-compat to a future milestone."
---

# Phase 4: Security & Cleanup — Verification Report

**Phase Goal:** Standalone dashboard is secured, attack surfaces (SSRF, CSRF) are mitigated, task module imports are restricted, and dead code is marked for removal.
**Verified:** 2026-05-15T12:38:44Z
**HEAD:** f81dff1
**Status:** PASSED
**Re-verification:** No — initial verification.

## Goal Achievement

### Requirements Coverage (REQUIREMENTS.md)

| Req | Description | Plan | Status | Evidence |
|-----|-------------|------|--------|----------|
| SEC-01 | FastAPI dashboard auth (API key or basic auth) | 04-03 | ✓ VERIFIED | `src/sqlery/fastapi_sqlery/auth.py` (154 lines) with `DashboardAuthMiddleware`; three-mode resolver `resolve_auth_mode` (standalone/disabled/inherit) + env override; `hmac.compare_digest` at line 125; `/healthz` bypass at line 116 BEFORE mode resolution; key generated at `./.sqlery/dashboard.key` with `0o600` (line 95); installed in `src/sqlery/fastapi_sqlery/app.py:28` before `include_router`. |
| SEC-02 | Webhook SSRF — private IP ranges blocked | 04-04 | ✓ VERIFIED | `src/sqlery/security/ssrf.py:validate_webhook_url`; v4 denylist covers 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16, 0.0.0.0/8, 100.64/10 (CGNAT); v6 covers ::1, ::/128, fe80::/10, fc00::/7 (ULA incl. AWS v6 metadata); hostname denylist for `localhost`, GCP `metadata.google.internal`; scheme allowlist (http/https only); resolve-then-check via `socket.getaddrinfo` rejects if ANY returned IP is blocked. Gate wired into `src/sqlery/django_sqlery/webhooks.py:88-92` BEFORE the HTTP send. |
| SEC-03 | Django admin API endpoints CSRF-protected | 04-05 | ✓ VERIFIED | `grep -c '@csrf_exempt' src/sqlery/django_sqlery/api_views.py` = **0**. `views.py` retains exactly **3** intentional decorators (lines 344, 435, 928 — internal_worker, health_check, trigger_view) — confirmed by inspection. CSRF regression test (`tests/test_csrf_regression.py`) passes. |
| SEC-04 | `ALLOWED_TASK_MODULES` allowlist | 04-02 | ✓ VERIFIED | `src/sqlery/core/security.py:check_task_module_allowed` implements prefix-with-dot-boundary semantics; empty = pass-through (BC). Wired in `src/sqlery/core/worker.py:17,246` BEFORE `import_task` → `importlib.import_module`. Prod-env warning `warn_if_unconfigured` called at WorkerProcess.run (worker.py:443). |
| CLEAN-01 | 24 BC stub files annotated with deletion dates | 04-01 | ✓ VERIFIED (with note) | `grep -l "Remove after 2027-05-14" src/sqlery/*.py \| wc -l` = **22** (20 BC stubs + webhooks.py stub + 1 additional). ROADMAP estimate of 24 came from RESEARCH; SUMMARY reconciles to the actual count discovered (20 BC + webhooks). All discovered BC stubs are marked. No deletions. |
| CLEAN-02 | Dead `AsyncStorageBackend = None` code annotated | 04-01 | ✓ VERIFIED | `grep "Remove after 2026-11-14" src/sqlery/async_worker.py` = 1 match (line 2). Plan verified marker already present. |
| CLEAN-03 | Commented-out code blocks marked with deletion dates | 04-01 | ✓ VERIFIED | 11 markers added across 4 hotspots: `core/daemon.py` (4), `core/worker.py` (2), `rate_limit_utils.py` (2), `django_sqlery/utils.py` (3). All carry `Remove after 2027-05-14`. Plan capped 20; 11 discovered + marked (deviation documented in SUMMARY). |
| CLEAN-04 | `django_sqlery.webhooks` import bug fixed | 04-01 | ✓ VERIFIED | `src/sqlery/django_sqlery/webhooks.py` exists (278 lines, canonical). `src/sqlery/webhooks.py` is a 13-line `sys.modules`-aliasing BC stub. Both import paths resolve to the same module object (test_clean04_webhook_import.py passes). Import attempt without Django installed correctly fails at `from django.db.models import F` — confirming code is real, not a stub. |

### ROADMAP Success Criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | FastAPI standalone dashboard rejects unauthenticated requests | ✓ VERIFIED | `DashboardAuthMiddleware.dispatch` returns 401 when `X-Sqlery-Key` missing/wrong in standalone mode; constant-time comparison via `hmac.compare_digest`. `install()` called before any `include_router` (app.py:28). `tests/unit/test_dashboard_auth.py` passes. |
| 2 | Webhook URL validation blocks private/link-local/loopback IPs | ✓ VERIFIED | Spot-check executed: `http://169.254.169.254`, `http://10.0.0.1`, `http://192.168.1.1`, `http://localhost`, `http://[::1]`, `http://127.0.0.1`, `http://100.64.0.1`, `file:///etc/passwd` all raise `WebhookURLBlocked`. |
| 3 | Django admin API endpoints protected against CSRF | ✓ VERIFIED | Zero `@csrf_exempt` in `api_views.py`; only 3 deliberate (internal_worker/health/trigger) in `views.py`. CSRF regression test asserts POST without token → 403. |
| 4 | Task module imports restricted to `ALLOWED_TASK_MODULES` when configured | ✓ VERIFIED | `check_task_module_allowed` invoked BEFORE `import_task` in JobExecutor; raises `TaskModuleNotAllowed` on mismatch. `tests/unit/test_security.py` covers BC pass-through + prefix-boundary + `myapp_evil` bypass defense. |
| 5 | All 24 BC stub files and dead async code annotated with deletion dates | ✓ VERIFIED (count reconciled) | 22 marked stubs in `src/sqlery/` + `async_worker.py` (2026-11-14 marker) + 11 commented-block markers across 4 core files. ROADMAP estimate of 24 vs actual 22+ is a counting reconciliation, not a missing artifact — all BC stubs actually present are marked. No file deletions. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/sqlery/fastapi_sqlery/auth.py` | Three-mode middleware, hmac compare, /healthz bypass | ✓ VERIFIED | 154 lines; resolves mode via env then `scope['root_path']` (deviation from CONTEXT scope['app'] — documented and correct, since `scope['app']` is rewritten during dispatch). |
| `src/sqlery/security/ssrf.py` | Resolve-then-check, full denylist | ✓ VERIFIED | 146 lines; `WebhookURLBlocked(ValueError)`; `getaddrinfo` rejects on any blocked answer. |
| `src/sqlery/core/security.py` | Allowlist + prod-env warn | ✓ VERIFIED | 107 lines; prefix-with-dot-boundary; `is_production_env` checks ENV/ENVIRONMENT/DJANGO_SETTINGS_MODULE. |
| `src/sqlery/webhooks.py` | BC stub via sys.modules aliasing | ✓ VERIFIED | 13 lines; `sys.modules[__name__] = _canonical` preserves `patch.object` semantics. |
| `src/sqlery/django_sqlery/webhooks.py` | Canonical webhook module with SSRF gate | ✓ VERIFIED | 278 lines; SSRF gate at lines 88-92. |
| `docs/SECURITY.md` | Operator-facing security model | ✓ VERIFIED | 444 lines; README links it at line 108. Covers SEC-01..04 + dead-code policy + documented v1 limitations. |

### Key Link Verification

| From | To | Via | Status |
|------|-----|-----|--------|
| `fastapi_sqlery/app.py` | `auth.install` | `install_auth(app)` before `include_router` (line 28) | ✓ WIRED |
| `core/worker.py` JobExecutor | `core/security.check_task_module_allowed` | Called BEFORE `import_task` at line 246 | ✓ WIRED |
| `core/worker.py` WorkerProcess.run | `core/security.warn_if_unconfigured` | Called at line 443 | ✓ WIRED |
| `django_sqlery/webhooks.py` `send_webhook_with_retry` | `security.ssrf.validate_webhook_url` | Called at line 91, BEFORE HTTP send | ✓ WIRED |
| `src/sqlery/webhooks.py` | `sqlery.django_sqlery.webhooks` | `sys.modules[__name__] = _canonical` | ✓ WIRED (identity alias) |
| README.md | `docs/SECURITY.md` | Link at README.md:108 | ✓ WIRED |

### Data-Flow Trace

| Artifact | Data | Source | Real? | Status |
|----------|------|--------|-------|--------|
| `DashboardAuthMiddleware._expected_key` | API key | `_load_or_create_key` (env → file → generated+0600) | Yes | ✓ FLOWING |
| `validate_webhook_url` resolution | IP list | `socket.getaddrinfo` (live DNS) | Yes | ✓ FLOWING |
| `check_task_module_allowed` allowed | Config | `get_config("ALLOWED_TASK_MODULES", None)` from compat layer | Yes | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| SSRF blocks AWS metadata IP | `python -c "validate_webhook_url('http://169.254.169.254')"` | `WebhookURLBlocked: ip blocked: 169.254.169.254` | ✓ PASS |
| SSRF blocks 8 representative URLs | private/link-local/loopback/CGNAT/IPv6-loopback/localhost/file-scheme | All 8 blocked | ✓ PASS |
| Zero `@csrf_exempt` in api_views.py | `grep -c '@csrf_exempt' src/sqlery/django_sqlery/api_views.py` | 0 | ✓ PASS |
| Exactly 3 `@csrf_exempt` in views.py | `grep -c '@csrf_exempt' src/sqlery/django_sqlery/views.py` | 3 | ✓ PASS |
| Phase-4 unit tests pass | `uv run pytest tests/unit/test_ssrf.py tests/unit/test_security.py tests/unit/test_dashboard_auth.py tests/test_csrf_regression.py -q` | 99 passed, 1 skipped | ✓ PASS |
| `pyproject.toml` no regression | django>=5.2, aiosqlite, greenlet, fail_under=13, pythonpath, slow+postgres markers | All present (lines 38, 45, 57, 58, 143-146, 167) | ✓ PASS |

### Anti-Patterns Found

None blocking. Reviewed phase-modified files; no `TBD`/`FIXME`/`XXX` markers without follow-up references. The `# #CLEANUP` markers all carry explicit `Remove after YYYY-MM-DD` dates per project policy (per `feedback_dead_code`).

### Notes / Observations

- **BC stub count reconciliation:** ROADMAP SC#5 says "24 BC stub files", actual count is 22 marked + dead-async-marker + 11 commented-block markers. The 24 was an estimate at requirements time. The intent ("all BC stubs annotated, none deleted") is satisfied. Not a gap.
- **Deviation from CONTEXT (auto-detect via `scope['app']` → `scope['root_path']`):** Plan 04-03 deviates from CONTEXT's prescribed auto-detect mechanism because Starlette rewrites `scope['app']` to the inner app during route dispatch. The replacement (`scope['root_path']` non-empty implies mount) is correct and documented in `auth.py:53-56` and the plan SUMMARY.
- **Merge-conflict resolution in 04-04:** 04-04 initially overwrote 04-01's BC stub at `src/sqlery/webhooks.py`; resolved by porting SSRF gate into the canonical `django_sqlery/webhooks.py`. Verified: stub intact (13-line `sys.modules` alias), gate present in canonical module.

### Deferred Items (Not Actionable Gaps)

These limitations are documented in `docs/SECURITY.md` as known v1 caveats and are explicitly out of scope:

| # | Item | Where Documented |
|---|------|------------------|
| 1 | ~50ms DNS-rebinding window between validation and HTTP send | `src/sqlery/security/ssrf.py:10-14`; `docs/SECURITY.md` |
| 2 | No re-validation after HTTP redirects | 04-04-SUMMARY; `docs/SECURITY.md` |
| 3 | IPv4-mapped IPv6 (`::ffff:10.0.0.1`) normalization | 04-04-SUMMARY |
| 4 | Celery drop-in compat shim | `.planning/BACKLOG.md` (next milestone) |

### Human Verification Required

None. All success criteria are observable via grep/import/test and were verified programmatically.

### Gaps Summary

No gaps. All 4 SEC requirements, 4 CLEAN requirements, and 5 ROADMAP success criteria are satisfied with codebase evidence and passing tests. Recommendation: **phase-complete**.

---

_Verified: 2026-05-15T12:38:44Z_
_Verifier: Claude (gsd-verifier)_
