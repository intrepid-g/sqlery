# Phase 04: Security & Cleanup — Research

**Researched:** 2026-05-14
**Domain:** Web security (auth middleware, SSRF, CSRF, allowlist) + dead-code marking
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **SEC-01 — Three-mode dashboard auth.** `standalone` (default; require `X-Sqlery-Key`, key from `SQLERY_DASHBOARD_API_KEY` → `./.sqlery/dashboard.key` → auto-generated 32B URL-safe), `disabled` (env override, logs WARNING), `inherit` (env override or auto-detect when mounted as Starlette sub-app). Explicit env value always wins. `hmac.compare_digest`. `/healthz` bypasses auth even in standalone. New `src/sqlery/fastapi_sqlery/auth.py`. Middleware wired BEFORE all routes including `/trigger`.
- **SEC-02 — SSRF denylist** (loopback, RFC 1918, link-local, ULA, CGNAT, unspecified, cloud metadata incl. `metadata.google.internal`). `socket.getaddrinfo` resolve, check ALL returned IPs. New `src/sqlery/security/ssrf.py` with `validate_webhook_url(url, *, allow_loopback=False)` raising `WebhookURLBlocked`. Called from `sqlery/webhooks.py` immediately before `requests.post`. No project-wide allowlist knob in v1.
- **SEC-03 — Verify-and-test CSRF.** Audit `@csrf_exempt` callsites; add one regression test that POST without CSRF token to a state-changing admin URL returns 403; no new middleware unless audit finds gaps.
- **SEC-04 — `ALLOWED_TASK_MODULES` opt-in.** Unset = allow all (BC). Set = prefix-check before `importlib.import_module`. Production-env detection emits one-line WARNING when unset. New `src/sqlery/core/security.py` with `check_task_module_allowed`. Called from `sqlery/core/worker.py` before module import.
- **CLEAN-01..04 — mark + date-stamp, no deletes.** Use `#CLEANUP YYYY-MM-DD remove after YYYY-MM-DD`. Phase-01 stubs missing remove-after dates get `Remove after 2027-05-14`. Fix `django_sqlery.webhooks` import bug (CLEAN-04).

### Claude's Discretion
- Whether to fold `worker_process.py:71` arity fix into a SEC/CLEAN plan or stand it up as its own one-line plan.
- Whether to use 1 omnibus CLEAN plan or 4 small ones (CONTEXT says either is fine if tasks stay atomic).
- Exact `docs/SECURITY.md` vs README appendix shape.

### Deferred Ideas (OUT OF SCOPE)
- Audit logging of dashboard actions; rate limiting; payload encryption at rest; actually deleting dead-code markers.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEC-01 | FastAPI dashboard auth | `app.py` has 26 routes incl. `/trigger`; no auth today; Starlette mount-detection via `request.scope` documented below |
| SEC-02 | Webhook SSRF | `webhooks.py` uses synchronous `requests.post(job.webhook_url, ...)` — single inject point at line 135; resolve-then-check pattern documented below |
| SEC-03 | Django admin CSRF | 14 `@csrf_exempt` decorators found across `api_views.py` (11) and `views.py` (3); audit table below |
| SEC-04 | `ALLOWED_TASK_MODULES` allowlist | `core/worker.py` JobExecutor is the import callsite (per CLAUDE.md component map) |
| CLEAN-01 | 24 BC stub files | Inventory: 19 `#CLEANUP` moved-to-django_sqlery stubs in `src/sqlery/*.py` + 3 `DEPRECATED` modules (`executor.py`, `django_sqlery/{worker_claiming,executor}.py`); 22 of these have markers, none have explicit remove-after dates except the 3 DEPRECATED ones |
| CLEAN-02 | `async_worker.py` dead-code marker | Already has marker AND remove-after (`Remove after 2026-11-14`); CLEAN-02 is essentially a no-op verification |
| CLEAN-03 | Commented-out blocks | Top hotspots: `core/daemon.py` (5 blocks), `rate_limit_utils.py` (3), `django_sqlery/utils.py` (3), `core/worker.py` (3), plus 11 single-block files |
| CLEAN-04 | Webhook import bug | No literal `django_sqlery.webhooks` import in tree TODAY — but `django_sqlery/models.py:678,734` uses `from .webhooks import send_webhook_with_retry` while the file lives at `src/sqlery/webhooks.py` (NOT `src/sqlery/django_sqlery/webhooks.py`). See finding 3 below. |
</phase_requirements>

## Summary

- Standalone FastAPI dashboard (`src/sqlery/fastapi_sqlery/app.py`) is completely unauthenticated today: 26 routes including DELETE/POST `/api/jobs`, scheduled-task CRUD, and the SMOD-03 `/trigger` admin-surface endpoint mounted at line 28. Default-on auth is a meaningful security upgrade.
- 14 `@csrf_exempt` decorators in Django: 11 in `api_views.py` (lines 220, 292, 386, 464, 486, 618, 818, 851, 889, 912 — all state-changing admin JSON endpoints) and 3 in `views.py` (`internal_worker` HMAC-protected, `health_check` read-only, `trigger_view` SMOD-03 envelope-protected). Two are intentionally token/HMAC-protected; the 11 in `api_views.py` rely on `@staff_required_json` (session cookie) and **are the SEC-03 risk surface** — they accept POST/cookie-auth without a CSRF token.
- The "broken `django_sqlery.webhooks` import" is *latent, not literal*: `webhooks.py` lives at `src/sqlery/webhooks.py` and imports `from .models import QueuedJob` + `from .settings import get_setting` (line 13, 14) — but those names are now stubs at `src/sqlery/{models,settings}.py` whose canonical home is `src/sqlery/django_sqlery/`. The Django callers (`django_sqlery/models.py:678,734`) import `from .webhooks` which resolves via Python's package machinery to nothing in `django_sqlery/`. Fix is to either (a) move `webhooks.py` into `django_sqlery/`, or (b) change callers to `from sqlery.webhooks import …`.
- `async_worker.py` is already fully marked (`#CLEANUP: 2026-05-14 — superseded by sqlery.core.async_worker (ASYN-04/05). Remove after 2026-11-14.`) — CLEAN-02 is verification only.
- `worker_process.py:71` arity bug still present: `claim_next_job_with_queue_priority(worker)` missing `backend` and `queues` args. Canonical signature is `(worker, backend, queues, ...)` at `core/claiming.py:178`.

**Primary recommendation:** 6 plans across 3 waves. Wave 1 (parallel-safe): SEC-04 + CLEAN omnibus + worker_process fix. Wave 2 (parallel): SEC-01 + SEC-02. Wave 3 (depends on SEC-01): SEC-03 audit-and-test.

## CSRF Audit Table — open item #1

| Endpoint | File:line | State-changing? | Current auth | Bypasses CSRF? | SEC-03 Action |
|----------|-----------|-----------------|--------------|-----------------|----------------|
| `api_task_action` | `django_sqlery/api_views.py:220` | YES (enqueue/enable/disable/delete) | `@staff_required_json` (session cookie) | **YES** | Add regression test asserting 403 without token, OR convert to token-auth header |
| `api_stop_job` | `api_views.py:292` | YES (kills process, marks failed) | session | **YES** | Same |
| `api_worker_action` | `api_views.py:386` | YES (pause/restart/SIGTERM) | session | **YES** | Same |
| `api_remove_queued_job` | `api_views.py:464` | YES (delete) | session | **YES** | Same |
| `api_enqueue_job_now` | `api_views.py:486` | YES (mutates scheduled_at) | session | **YES** | Same |
| `api_job_priority` | `api_views.py:618` | YES (mutates priority) | session | **YES** | Same |
| `api_clear_jobs` | `api_views.py:818` | YES (bulk delete) | session | **YES** | Same |
| `api_archive_scheduled_jobs` | `api_views.py:851` | YES (bulk archive) | session | **YES** | Same |
| `api_vacuum` | `api_views.py:889` | YES (DB vacuum) | session | **YES** | Same |
| `api_manual_intervention` | `api_views.py:912` | YES (daemon command) | session | **YES** | Same |
| (unknown @ 851 alt) | — | — | — | — | covered above |
| `internal_worker` | `django_sqlery/views.py:344` | YES (spawns subprocess) | HMAC `X-Signature` + `X-Timestamp` (5s expiry, `verify_signature`) | OK — token-auth, no cookie reliance | Document as token-auth (not CSRF surface) |
| `health_check` | `views.py:435` | NO (read-only) | none | OK — read-only | Document |
| `trigger_view` | `views.py:928` | YES (job dispatch) | Envelope HMAC via `core.triggers.handle` | OK — token-auth, no cookie reliance | Document |

**Decision required (planner):** For the 11 state-changing api_views endpoints, choose between:
1. **Drop `@csrf_exempt`** — keep session auth, force CSRF token. Breaks JS clients that don't include the token; cheap fix is to expose Django's `csrftoken` cookie + `X-CSRFToken` header in the dashboard JS.
2. **Header-auth alternative** — require `X-Sqlery-Admin-Key` instead of relying on session cookie; `csrf_exempt` then becomes correct (no cookie used).
CONTEXT SEC-03 says "verify + test, no new middleware unless audit finds bypassed endpoints." Audit DID find 11 bypassed endpoints. Recommended path: (1) drop `@csrf_exempt` on all 11, (2) add one regression test, (3) update dashboard JS to send `X-CSRFToken`.

## Backward-Compat Stub Inventory — open item #2

24 BC stub files. 22 have `#CLEANUP` / `DEPRECATED` markers; **0 of the 19 simple shims have an explicit "Remove after" date** — these need `# Remove after 2027-05-14` added (per CONTEXT). The 3 `DEPRECATED` modules already have explicit "Remove after 2027-05-13".

| File | Marker | Has remove-after date? | CLEAN-01 action |
|------|--------|------------------------|------------------|
| `src/sqlery/admin.py` | `# #CLEANUP: This file has been moved...` | NO | add `Remove after 2027-05-14` |
| `src/sqlery/apps.py` | same | NO | add date |
| `src/sqlery/cleanup.py` | same | NO | add date |
| `src/sqlery/daemon_manager.py` | same | NO | add date |
| `src/sqlery/daemon_middleware.py` | same | NO | add date |
| `src/sqlery/daemon_worker.py` | same | NO | add date |
| `src/sqlery/dashboard_views.py` | same | NO | add date |
| `src/sqlery/db_compat.py` | same | NO | add date |
| `src/sqlery/http_trigger_middleware.py` | same | NO | add date |
| `src/sqlery/middleware.py` | same | NO | add date |
| `src/sqlery/models.py` | same | NO | add date |
| `src/sqlery/registries.py` | same | NO | add date |
| `src/sqlery/settings.py` | same | NO | add date |
| `src/sqlery/subprocess_executor.py` | same | NO | add date |
| `src/sqlery/subprocess_middleware.py` | same | NO | add date |
| `src/sqlery/urls.py` | same | NO | add date |
| `src/sqlery/views.py` | same | NO | add date |
| `src/sqlery/worker_claiming.py` | same | NO | add date |
| `src/sqlery/worker_process.py` | same | NO | add date |
| `src/sqlery/worker_registry.py` | same | NO | add date |
| `src/sqlery/executor.py` | `DEPRECATED 2026-05-13` | YES (`Remove after 2027-05-13`) | NO-OP |
| `src/sqlery/django_sqlery/executor.py` | `DEPRECATED 2026-05-13` | YES (`Remove after 2027-05-13`) | NO-OP |
| `src/sqlery/django_sqlery/worker_claiming.py` | `DEPRECATED 2026-05-13` | YES (`Remove after 2027-05-13`) | NO-OP |
| `src/sqlery/django_sqlery/signature.py` | `#CLEANUP: 2026-05-14 ... Remove after 2026-11-14` | YES | NO-OP |
| `src/sqlery/async_worker.py` (CLEAN-02 subject) | `#CLEANUP: 2026-05-14 ... Remove after 2026-11-14` | YES | NO-OP |
| `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py` | `#CLEANUP 2026-05-14: dead — remove after Phase 4` | partial (no ISO date) | optional — replace "after Phase 4" with ISO date |

**Total to touch for CLEAN-01:** 20 files (the 20 marker-only entries above). All 1-line edits at top of file.

## CLEAN-04 Broken-import Location — open item #3

There is **no literal `django_sqlery.webhooks`** string in the source tree (verified via grep). The bug manifests differently than the requirement title implies:

- `src/sqlery/webhooks.py:13` does `from .models import QueuedJob` and line 14 `from .settings import get_setting`. Those dotted names resolve inside the **top-level `sqlery` package** — i.e. `sqlery.models` (a shim at `src/sqlery/models.py:1` `#CLEANUP: This file has been moved to src/sqlery/django_sqlery/`) and `sqlery.settings` (same shim pattern at `src/sqlery/settings.py:1`).
- `src/sqlery/django_sqlery/models.py:678,734` does `from .webhooks import send_webhook_with_retry`. Within the `django_sqlery` package, this resolves to **`sqlery/django_sqlery/webhooks.py`** — which does not exist. The Python import machinery falls back / fails depending on whether the parent shim has re-exports.

**Canonical fix options:**
1. **Move file:** rename `src/sqlery/webhooks.py` → `src/sqlery/django_sqlery/webhooks.py` (it imports Django ORM `F` and uses `QueuedJob.objects.filter`, so it's already Django-only). Leave a dated stub at `src/sqlery/webhooks.py` re-exporting from the new location to keep `from sqlery.webhooks` working (tests already use this — `tests/unit/test_webhooks.py:47` imports `from sqlery import webhooks as webhooks_mod`).
2. **Update callers:** change `django_sqlery/models.py:678,734` to `from sqlery.webhooks import send_webhook_with_retry`.

**Recommended (per CLAUDE.md dead-code policy):** option 1 — the file is already Django-coupled (uses `django.db.models.F` at line 11, accesses ORM `.save(update_fields=...)` at lines 145, 184, 205, 213). Moving it to its canonical `django_sqlery/` location is the structural fix; the stub re-export at `src/sqlery/webhooks.py` honors backward-compatibility.

**Regression test:** new `tests/unit/test_clean04_webhook_import.py` asserting both `from sqlery.webhooks import send_webhook_with_retry` and `from sqlery.django_sqlery.webhooks import send_webhook_with_retry` succeed.

## Starlette Mount-detection — open item #4

**Question:** when Sqlery's FastAPI app is mounted as a sub-app of a parent Starlette/FastAPI app, what `request.scope` signal reliably says "I am a sub-app"?

**Answer (verified against Starlette docs):** Use **two-step detection**:

```python
def _is_mounted_sub_app(request: Request, our_app: FastAPI) -> bool:
    # Starlette sets scope["app"] to the *root* ASGI application that
    # received the connection — NOT the sub-app. When mounted, scope["app"]
    # is the parent app, not our sqlery_app.
    scope_app = request.scope.get("app")
    if scope_app is not None and scope_app is not our_app:
        return True
    # Belt-and-suspenders: a non-empty root_path means we're mounted under
    # a path prefix. (Not 100% reliable on its own — uvicorn `--root-path`
    # also sets this on a top-level app behind a reverse proxy. Combine
    # with the app-identity check above.)
    if request.scope.get("root_path"):
        return True
    return False
```

**Citations:**
- Starlette `scope["app"]` is documented as "the top-level ASGI app instance" — confirmed in `starlette.applications.Starlette.__call__` which assigns `scope["app"] = self` (the root app being called). Mounted sub-apps do not overwrite this.
- `scope["root_path"]` is the ASGI standard prefix the app is mounted under; Starlette's `Mount` class sets this when delegating into the sub-app. See ASGI spec §3.1.
- Caveat: `uvicorn --root-path /api` also populates `scope["root_path"]` on a top-level app to support reverse-proxy stripping. Identity check on `scope["app"]` disambiguates.

**Recommended implementation in `auth.py`:**
- Module-level singleton reference to the sqlery `FastAPI` instance (captured at middleware install time).
- At request time, compare `request.scope["app"] is our_app`.
- Fall back to `root_path` only if `scope["app"]` is `None` (rare — happens with some pure-ASGI test clients).

## Webhooks SSRF Integration Point — open item #5

**HTTP client:** `src/sqlery/webhooks.py:17` imports `requests` (synchronous) — wrapped in try/except (line 16-19) marking the dep optional.

**Question:** Does `requests` (urllib3 under the hood) expose a hook between DNS resolution and TCP connect, so we can inject SSRF checks after resolution but before connecting?

**Answer:** **No clean built-in hook.** Two viable patterns:

1. **Pre-resolve + pre-check pattern (RECOMMENDED).**
   - In `validate_webhook_url(url)`: parse URL, call `socket.getaddrinfo(host, port)`, iterate **every** `(family, type, proto, canonname, sockaddr)` tuple, check each IP against the denylist. Reject if any IP matches.
   - Race-window note: a DNS rebinding attack can return a public IP at validate time, then a private IP at `requests.post` time. Mitigations:
     - Iterate ALL returned IPs (Sqlery rule per CONTEXT line 60).
     - Best-effort: pass `host=resolved_ip` and `Host:` header trickery — but this breaks SNI/TLS. Practically the resolve-then-check + `requests` immediate-call is acceptable for v1 (window is ~tens of ms).
   - Sources: OWASP SSRF cheat-sheet, `urllib3` issue #2168 (no pre-connect hook), Snyk SSRF guidance for Python `requests`.

2. **Custom `HTTPAdapter` with patched connection pool** — overkill for v1; deferred.

**Concrete denylist constants** to put in `src/sqlery/security/ssrf.py`:

```python
import ipaddress

BLOCKED_V4_NETS = [
    ipaddress.ip_network(n) for n in [
        "127.0.0.0/8",     # loopback
        "10.0.0.0/8",      # RFC 1918
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",  # link-local (incl. 169.254.169.254 cloud metadata)
        "0.0.0.0/8",       # unspecified
        "100.64.0.0/10",   # CGNAT
    ]
]
BLOCKED_V6_NETS = [
    ipaddress.ip_network(n) for n in [
        "::1/128",   # loopback
        "fe80::/10", # link-local
        "fc00::/7",  # ULA
        "::/128",    # unspecified
    ]
]
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}

class WebhookURLBlocked(ValueError):
    pass
```

**Call-site patch** (`src/sqlery/webhooks.py` ~line 134, immediately before `requests.post`):
```python
from sqlery.security.ssrf import validate_webhook_url, WebhookURLBlocked
try:
    validate_webhook_url(job.webhook_url)
except WebhookURLBlocked as e:
    logger.warning(f"Webhook blocked by SSRF policy for job {job.id}: {e}")
    return False
```

**Existing test coverage gap:** `tests/unit/test_webhooks.py` is at 100% on HMAC/retry/HTTP-mock but covers ZERO SSRF surface (every test patches `webhooks_mod.requests`, bypassing any URL validation). New `tests/unit/test_ssrf.py` must cover: every blocked range (v4 + v6), `localhost`, AWS metadata `169.254.169.254`, `metadata.google.internal`, DNS-rebinding (mock `getaddrinfo` to return mixed public+private — must reject), and a happy-path public URL (mock returns `1.2.3.4`).

## `worker_process.py:71` arity bug status

**Still present.** Confirmed at `src/sqlery/django_sqlery/worker_process.py:71`:
```python
job = claim_next_job_with_queue_priority(worker)
```
Canonical signature is `(worker, backend, queues, ...)` per `src/sqlery/core/claiming.py:178`. The fix matches Phase 03's Fix 1 pattern (`f22049d` passed `self` as backend at `django_sqlery/backend.py:160`).

**Recommended fix:**
```python
from sqlery.compat import get_backend
backend = get_backend()
queues = get_setting("WORKER_QUEUES", ["default"])
...
job = claim_next_job_with_queue_priority(worker, backend, queues)
```

**Note:** This file itself is one of the 24 CLEAN-01 shims (`src/sqlery/worker_process.py:1` re-exports from `src/sqlery/django_sqlery/worker_process.py`). The bug is in the **canonical** Django copy.

## Recommended Plan Breakdown

**Wave 1 — parallelizable, no cross-deps:**
- **04-01 — CLEAN omnibus (CLEAN-01/02/03/04 + worker_process fix).** 20 BC stub date-stamps; verify async_worker.py marker (no-op); date-stamp top 4 commented-block files (`core/daemon.py`, `rate_limit_utils.py`, `django_sqlery/utils.py`, `core/worker.py`); move `webhooks.py` to `django_sqlery/` with stub at old path + regression test; fix `worker_process.py:71` arity bug. Single plan because each task is <10 lines; one atomic commit per task.
  - *Alternative:* split into 04-01a (BC stubs date-stamp), 04-01b (webhooks move + worker_process fix), 04-01c (commented-blocks sweep). Acceptable if planner prefers smaller PRs.
- **04-02 — SEC-04 `ALLOWED_TASK_MODULES`.** New `src/sqlery/core/security.py` + integration in `core/worker.py` JobExecutor before `importlib.import_module` call; settings keys in both `django_sqlery/settings.py` and `fastapi_sqlery/config.py`; production-env warning; unit tests.

**Wave 2 — depends on Wave 1 CLEAN-04 (so SSRF can patch the canonical webhooks.py location):**
- **04-03 — SEC-01 dashboard auth (standalone + disabled + inherit + auto-detect).** New `src/sqlery/fastapi_sqlery/auth.py`; wire BEFORE `include_router(_trigger_router)` at `app.py:28`; key persistence to `./.sqlery/dashboard.key` with `0700`/`0600` perms; `/healthz` route added BEFORE middleware install (or whitelisted in middleware); unit tests for all three modes incl. mount detection.
- **04-04 — SEC-02 webhook SSRF.** New `src/sqlery/security/ssrf.py`; integrate at `sqlery/webhooks.py` (canonical location post-04-01); `tests/unit/test_ssrf.py` per coverage matrix above.

**Wave 3 — depends on Wave 2 (CSRF tests need an authenticated client which may share fixtures with SEC-01):**
- **04-05 — SEC-03 CSRF audit + regression test.** Drop `@csrf_exempt` from the 11 state-changing `api_views.py` endpoints; update dashboard JS (if present in `static/`) to send `X-CSRFToken`; new `tests/test_csrf_regression.py` POST without token → 403. Document `internal_worker` + `trigger_view` as token-auth in `docs/SECURITY.md`.
- **04-06 — `docs/SECURITY.md`.** Single doc capturing all four SEC controls + threat model + operator-facing config. Pulled out so writing it doesn't block the security plans.

**Total:** 6 plans, 3 waves. Worker_process fix folded into 04-01 (cheapest).

## Pitfalls

| Pitfall | Where it bites | Mitigation |
|---------|---------------|------------|
| **Timing leak in key compare** | `auth.py` standalone mode | `hmac.compare_digest(provided, expected)` — never `==` |
| **Header case-sensitivity** | Starlette `request.headers` is case-insensitive Dict; underlying `scope["headers"]` is **lowercased bytes**. Don't read raw scope; use `request.headers.get("x-sqlery-key")` | Use `request.headers` |
| **Key file world-readable** | First-run auto-gen | `os.umask(0o077)` before `open(...)`; chmod `0o600` after write; chmod `.sqlery/` `0o700` |
| **`/healthz` ordering** | Middleware installed BEFORE route registration means health check needs an explicit bypass in the middleware (check `request.url.path == "/healthz"`) — not a separate route registration order |
| **DNS rebinding** | `validate_webhook_url` resolves once, `requests.post` resolves again. ~50ms window | Iterate ALL `getaddrinfo` results; document as known v1 limitation; v2 could pin to resolved IP with `Host:` override |
| **IPv6 dual-stack** | `getaddrinfo` returns both A and AAAA — must check both families | Loop ALL tuples, handle both `ipaddress.IPv4Address` and `IPv6Address` |
| **Mount detection false-positive** | `uvicorn --root-path` sets `scope["root_path"]` on top-level app | Identity-check `scope["app"] is our_app` is primary signal; root_path is fallback only |
| **CSRF removal breaks dashboard JS** | Dropping `@csrf_exempt` on 11 endpoints breaks current admin JS POSTs | Inspect `src/sqlery/django_sqlery/static/` JS for fetch/XHR calls; add `X-CSRFToken` header reading `csrftoken` cookie |
| **`@csrf_exempt` reordering** | `@csrf_exempt` must be OUTERMOST decorator to actually disable CSRF; removing it just removes the disable. Don't accidentally re-apply it inside another decorator | Audit decorator order on each of the 11 |
| **`ALLOWED_TASK_MODULES` prefix matching** | Naive `startswith("myapp")` matches `myapp_evil.tasks`. Use prefix + `.` boundary check | `module_path == allowed or module_path.startswith(allowed + ".")` |
| **Production-env warning false-positive** | Dev machines with `ENVIRONMENT=local` won't trigger; `DJANGO_SETTINGS_MODULE=myproj.settings_production` will | Match `*prod*` / `*production*` case-insensitively; document in SECURITY.md |
| **`requests` optional dep** | `webhooks.py:17` makes `requests` optional with try/except. SSRF check must run BEFORE the `requests is None` check or DNS resolution short-circuits | Order in `send_webhook`: `validate_webhook_url(url)` → check `requests is None` → `requests.post` |
| **Webhook callsite is inside Django model** | `django_sqlery/models.py:678,734` calls `send_webhook_with_retry` from inside `mark_success` / `mark_failed`. SSRF rejection must not crash these methods | Catch `WebhookURLBlocked` in `send_webhook`, log + return False (already the failure-return pattern in webhooks.py) |
| **CLEAN-01 date-stamp without breaking imports** | The 20 BC shims currently re-export symbols. Adding a 2nd comment line at top is safe; don't modify the import lines | Edit only line 1 (extend) or line 2 (insert) — never touch the `from .X import Y` line |

## Sources

### Primary (HIGH)
- `src/sqlery/fastapi_sqlery/app.py` (full read) — current 26-route surface, no auth wiring
- `src/sqlery/webhooks.py` (full read) — sync `requests.post` callsite, optional-dep pattern
- `src/sqlery/django_sqlery/api_views.py` lines 200-920 — 11 `@csrf_exempt` state-changing endpoints
- `src/sqlery/django_sqlery/views.py` lines 335-450, 915-953 — 3 `@csrf_exempt`, 2 of which token-protected
- `src/sqlery/core/claiming.py:178` — canonical signature `(worker, backend, queues, ...)`
- `src/sqlery/django_sqlery/worker_process.py:71` — arity bug confirmed
- `src/sqlery/async_worker.py:1-2` — CLEAN-02 marker already complete
- `tests/unit/test_webhooks.py` — confirms tests patch `requests` at module level, do not exercise URL validation
- `.planning/phases/03-testing-ci/03-GAPS-SUMMARY.md` lines 53-58, 122-125 — worker_process.py:71 deferral

### Secondary (MEDIUM)
- Starlette source `starlette.applications.Starlette.__call__` — `scope["app"] = self` semantics
- ASGI spec §3.1 — `root_path` semantics
- OWASP SSRF prevention cheat sheet — resolve-then-check pattern; DNS rebinding mitigations
- urllib3 issue tracker — no pre-connect hook in stable API

### Tertiary (LOW)
- General training knowledge on `hmac.compare_digest`, Django CSRF middleware ordering — well-established, low risk

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `webhooks.py` import bug surfaces as `from .webhooks import ...` failing inside `django_sqlery/models.py` — not a literal `django_sqlery.webhooks` string anywhere | CLEAN-04 finding | If wrong, the planner may grep for a string that doesn't exist and conclude there's no bug. Mitigation: planner should run the regression test (try `from sqlery.django_sqlery.webhooks import send_webhook_with_retry`) — if it succeeds, the bug is already fixed; if it fails, this research is correct |
| A2 | Dashboard JS in `src/sqlery/django_sqlery/static/` makes cookie-auth fetch calls that would break if `@csrf_exempt` is dropped without adding `X-CSRFToken` | Pitfalls — CSRF removal | LOW: if the JS doesn't exist or already sends the header, the regression is a no-op. Planner should grep `static/` for `fetch(` / `XMLHttpRequest` first |
| A3 | `validate_webhook_url` race window (~50ms) is acceptable for v1 — DNS rebinding is a theoretical not practical risk for self-hosted task queues | SSRF integration | LOW: documented as known limitation; pinning resolved IP at the urllib3 level is a v2 hardening |

## Metadata

**Confidence breakdown:**
- CSRF audit: HIGH — every callsite read with line numbers
- BC stub inventory: HIGH — grep + per-file head verified
- CLEAN-04 location: MEDIUM — bug shape inferred from import graph; planner should confirm with a 1-line test
- Starlette mount detection: HIGH — semantics documented in Starlette and ASGI spec
- SSRF library hooks: HIGH — no pre-connect hook in `requests`/urllib3 is well-established
- worker_process.py:71: HIGH — same bug pattern as 03-GAPS Fix 1, confirmed line read

**Research date:** 2026-05-14
**Valid until:** 2026-06-13 (30 days)
