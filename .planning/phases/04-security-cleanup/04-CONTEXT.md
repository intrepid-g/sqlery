# Phase 04 — Security & Cleanup: Context

**Phase:** 04-security-cleanup
**Created:** 2026-05-14
**Source of scope:** ROADMAP.md Phase 4 + REQUIREMENTS.md (SEC-01..04, CLEAN-01..04)

## Canonical refs

- `.planning/ROADMAP.md` — Phase 4 (lines 64–80)
- `.planning/REQUIREMENTS.md` — SEC-01..04 (lines 60–63), CLEAN-01..04 (lines 67–70)
- `.planning/PROJECT.md` — security/cleanup motivations
- `.planning/phases/02-execution-modes/02-08-SUMMARY.md` — pure-core HTTP trigger (relevant for SEC-02 / SEC-03)
- `.planning/phases/03-testing-ci/03-GAPS-SUMMARY.md` — `worker_process.py:71` arity bug deferred here
- `CLAUDE.md` — dead-code policy, fork safety
- User auto-memory `feedback_dead_code` (`/Users/user/.claude/projects/-Users-user-Documents-GitHub-sqlery/memory/feedback_dead_code.md`) — comment-and-date-mark rather than delete

## Decisions (locked)

### SEC-01. Standalone dashboard auth — three-mode middleware (default ON)

The `sqlery-web` FastAPI dashboard ships an auth middleware that runs in one of three modes:

1. **Standalone (default).** Activated when `sqlery-web` is launched directly (the app is the top-level ASGI root). Requires `X-Sqlery-Key: <value>` on every request. Key sourced as:
   - `SQLERY_DASHBOARD_API_KEY` env var if set → use it.
   - Else read `./.sqlery/dashboard.key` if it exists (perms 0600).
   - Else generate a 32-byte URL-safe token, write to `./.sqlery/dashboard.key` (creating `.sqlery/` with `0700`), log the key ONCE to stderr at startup, persist.
2. **Disabled.** Activated by `SQLERY_DASHBOARD_AUTH=disabled` (case-insensitive). Middleware short-circuits to pass-through. Logs a one-line WARNING at startup so operators see they opted out.
3. **Inherit.** Activated either:
   - Explicitly: `SQLERY_DASHBOARD_AUTH=inherit`.
   - Auto-detect: at request time, check whether the Sqlery app is being served as a sub-app of a parent FastAPI/Starlette app (e.g. `request.scope.get("app") is not <sqlery_app>` and `request.scope.get("root_path")` is non-empty, or detect via Starlette's `app.routes` containing the sqlery app as a `Mount`). When inherited, skip Sqlery's auth check entirely — trust the parent's middleware/dependencies.

   Explicit `SQLERY_DASHBOARD_AUTH=inherit` always wins, even if auto-detect would say standalone. Explicit `=standalone` forces standalone mode even when mounted.

**Why:** Default-on closes the unauthenticated-admin-surface threat. The inherit path keeps the library composable when mounted into a customer's existing FastAPI app — common in real deployments. Auto-detect+override gives the cleanest UX for both groups.

**How to apply:**
- New module `src/sqlery/fastapi_sqlery/auth.py` containing `DashboardAuthMiddleware` (Starlette middleware) and the three-mode resolution helper `resolve_auth_mode(app, request) → Literal["standalone","disabled","inherit"]`.
- Wire the middleware in `src/sqlery/fastapi_sqlery/app.py` BEFORE all routes, including the `/trigger` route added by Plan 02-08 (the HTTP trigger receiver is admin-surface too).
- Standalone mode: 401 with body `{"detail":"unauthorized"}` on missing/bad key. Constant-time compare (`hmac.compare_digest`) to avoid timing leaks.
- Health endpoint `/healthz` (read-only liveness) MUST remain unauthenticated even in standalone mode — orchestrators/load balancers need it.
- Document the three modes in `README.md` or a new `docs/SECURITY.md`.
- Tests live in `tests/unit/test_dashboard_auth.py` covering all three modes + auto-detect heuristic + health-bypass + constant-time compare assertion.

### SEC-02. Webhook SSRF — block private + link-local + loopback + cloud metadata

Validate the destination of every webhook URL before delivery. Reject if the resolved host falls into any of:

- `127.0.0.0/8`, `::1/128` (loopback)
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918)
- `169.254.0.0/16`, `fe80::/10` (link-local)
- `fc00::/7` (IPv6 unique local)
- `0.0.0.0/8`, `::/128` (unspecified)
- `100.64.0.0/10` (CGNAT)
- AWS/GCP/Azure metadata: `169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`
- `localhost` and any hostname whose DNS resolution lands in the above

**Why:** Sqlery users can configure arbitrary webhook URLs; without validation a job's webhook can be used to exfiltrate creds from cloud metadata services or scan internal infrastructure.
**How to apply:**
- New module `src/sqlery/security/ssrf.py` with `validate_webhook_url(url: str, *, allow_loopback: bool = False) -> None` raising `WebhookURLBlocked` on rejection.
- Resolve hostname via `socket.getaddrinfo` and check ALL returned IPs (not just the first — defends DNS rebinding).
- Call from `src/sqlery/webhooks.py` immediately before `requests.post` / `httpx.post`.
- `allow_loopback=True` is opt-in for local testing only; never wired into production paths.
- Tests in `tests/unit/test_ssrf.py` cover every blocked range, DNS-rebinding (mock getaddrinfo returning a public IP then a private IP), and a happy-path public URL.
- Document that operators who legitimately need private-network webhooks must override at the `webhooks.py` callsite — there is intentionally no project-wide allowlist knob in v1 (avoids footguns).

### SEC-03. Django admin API CSRF — already protected, verify + test

Django's admin endpoints already use Django's CSRF middleware by default. The work here is to:
1. Audit `src/sqlery/django_sqlery/admin.py` and any custom views for `@csrf_exempt` decorators that bypass the protection.
2. Verify any state-changing API endpoint (POST/PUT/DELETE/PATCH) under the admin is CSRF-protected OR uses a token-auth alternative (e.g. an API key header that does NOT rely on cookies).
3. For non-cookie API surfaces (e.g. management API hit by service-to-service callers), document that they require token auth instead of CSRF.
4. Add a regression test that POSTing to a state-changing admin URL without a CSRF token returns 403.

**Why:** Verifying-and-locking is cheaper than rewriting; the framework is doing most of the work already.
**How to apply:** Audit + 1 regression test + a SECURITY.md note. No new middleware unless the audit finds bypassed endpoints.

### SEC-04. ALLOWED_TASK_MODULES — opt-in allowlist

Add config option `ALLOWED_TASK_MODULES: list[str] | None` (Django `DJANGO_SQL_JOBS["ALLOWED_TASK_MODULES"]` / standalone `SQLERY_ALLOWED_TASK_MODULES` env var as comma-separated).

Behavior:
- **Unset (default):** Allow all task module paths. BC-compatible. Emit a one-line WARNING at worker startup if `ALLOWED_TASK_MODULES` is unset AND the process detects it's running in a production-ish environment (env var `ENV`, `ENVIRONMENT`, or `DJANGO_SETTINGS_MODULE` value matching `*prod*` or `*production*`). The warning advises setting an allowlist.
- **Set to a list:** Before importing/executing a job, check that the task's module path starts with any of the allowed prefixes. Reject (mark job failed with `ImportError`-like error) if not.

**Why:** Restrictive-by-default would break every existing deployment; opt-in lets careful users harden. The production-warning nudges in the right direction without forcing migration.
**How to apply:**
- Implement in `src/sqlery/core/security.py` (new) with `check_task_module_allowed(module_path: str, allowed: list[str] | None) -> None` raising `TaskModuleNotAllowed`.
- Call from `src/sqlery/core/worker.py` immediately before `importlib.import_module(...)` in the job dispatch path.
- Tests: empty config = pass-through; configured allowlist = match prefix; non-match = raise.

### CLEAN-01..04. Mark + date-stamp; no deletes

Follow the user's existing `feedback_dead_code` memory: comment dead code with `#CLEANUP YYYY-MM-DD remove after YYYY-MM-DD` markers; do NOT delete anything.

- **CLEAN-01:** 24 backward-compatibility stub files — find them (`grep -rnE 'DEPRECATED \\d{4}' src/`), confirm each has a creation date and a "remove after" date. If the original Phase-01 stubs lack an explicit removal date, add `# Remove after 2027-05-14` (12 months from today). NO deletes.
- **CLEAN-02:** `src/sqlery/async_worker.py` (the dated stub left after Phase 02's AsyncWorker rewrite) — verify it already has `#CLEANUP` + remove-after marker. If yes, no-op. If no, add it.
- **CLEAN-03:** Sweep `src/sqlery/` for commented-out code blocks (`# Old:`, `# Removed:`, lines of `#` followed by `=`, `def`, etc.) and add date-stamps to whichever lack them. Use Python AST to identify multi-line comment blocks; manual review for ambiguous cases.
- **CLEAN-04:** Fix the import bug: `django_sqlery.webhooks` → `sqlery.webhooks`. Grep for the broken import path and fix all callsites. Add a regression test.

**Why (locked):** User explicitly prefers mark+date over delete. Deletion is a separate decision the user can make per-file when each marker's `remove after` date arrives.
**How to apply:** One plan per cleanup task (or a single CLEAN omnibus plan if the tasks are tiny). Tasks atomically committable.

## Specifics & gotchas

1. **`worker_process.py:71` arity bug** (from Phase 03 03-GAPS-SUMMARY) — same `claim_next_job_with_queue_priority` missing-`backend` arg at a non-test-covered callsite. Fold into Plan 04-X as a one-line fix. Out of SEC/CLEAN strictly but cheap to close.
2. **Phase 1 standalone-no-django CI human-verify** is still open. Phase 4 should NOT block on it; it's an independent ops task.
3. **Coverage gate** is at 13% with `[FOLLOWUP]` (Phase 03 03-08). Phase 4 should aim to NOT regress total coverage; adding tests for the new SEC modules helps move toward 70%.
4. **`tests/chaos/test_property_based.py`** is stubbed pending pipeline rewrite (Phase 03 03-GAPS). Phase 4 doesn't touch it.
5. **HTTP trigger / `/trigger` endpoint** (Phase 02 SMOD-03): the new SEC-01 middleware MUST protect this endpoint too. It's admin-surface — anyone who can POST to it can dispatch jobs.

## Deferred (not Phase 4)

- Audit logging of dashboard actions (who did what) — not in scope; future hardening.
- Rate-limiting on the dashboard — not in scope.
- Encrypting persisted job payloads at rest — not in scope.
- Actually deleting any dead-code markers — explicitly out of scope; that's a future quarterly cleanup gate.

## Open items for the researcher

1. Enumerate all `@csrf_exempt` decorators in `src/sqlery/django_sqlery/` — confirm whether any state-changing endpoints currently bypass CSRF.
2. Inventory the 24 backward-compat stub files: which have dates, which don't, file paths.
3. Find the `django_sqlery.webhooks` broken import (CLEAN-04) — cite file:line.
4. Verify Starlette's mount-detection mechanism (does `request.scope["app"]` reliably differ from the sqlery app when mounted? or do we need `request.scope["root_path"]` length check?).
5. Confirm whether `requests` (in `webhooks.py`) supports the urllib3 connection-pool hook needed to enforce SSRF AFTER DNS resolution but BEFORE TCP connect — otherwise we resolve manually and pre-check.

## Locked vs. negotiable

| Item | Status |
|---|---|
| Three-mode dashboard auth (standalone/disabled/inherit) with auto-detect+override | **LOCKED** |
| Per-project `./.sqlery/dashboard.key` storage | **LOCKED** (revisit if multi-tenant deployments complain) |
| SSRF denylist (RFC 1918 + link-local + loopback + cloud metadata + CGNAT) | **LOCKED** |
| `ALLOWED_TASK_MODULES` opt-in (unset = allow all) | **LOCKED** |
| Mark+date-stamp only — no deletes | **LOCKED** (per user `feedback_dead_code` memory) |
| Health endpoint `/healthz` bypasses auth even in standalone | **ASSUMED** — planner may revisit if it complicates the design |
| Key length = 32 bytes URL-safe | **ASSUMED** |
