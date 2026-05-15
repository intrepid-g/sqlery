# Sqlery Security Guide

This document describes Sqlery's security model and the controls available to
operators deploying it in production. It covers four hardening features landed
in Phase 04 and the project's dead-code retention policy.

If you are evaluating Sqlery for a deployment that processes sensitive data or
runs on shared infrastructure, read this document in full before you ship.

---

## Overview

Sqlery's threat model assumes the **worker process is trusted**: anything that
can enqueue a job can already cause arbitrary Python code to execute in the
worker, because that is precisely what enqueueing a job means. The protections
in this document harden the surfaces around the trusted worker — places where
input from a less-privileged actor (a dashboard user, a remote HTTP webhook
target, a job author who shouldn't be able to import arbitrary modules) crosses
into the worker's trust domain.

The four operator-controlled attack surfaces are:

1. **The dashboard / REST API** (`SEC-01`) — anyone who can reach the dashboard
   port can enqueue, cancel, or inspect jobs unless authentication is
   configured.
2. **Outbound webhooks** (`SEC-02`) — Sqlery posts job lifecycle events to a
   configured URL. Without SSRF protection, that URL can be pointed at internal
   networks or cloud metadata services.
3. **Django admin state-changing endpoints** (`SEC-03`) — these now require a
   CSRF token, like every other Django admin POST.
4. **Task module imports** (`SEC-04`) — an opt-in allowlist restricts which
   Python module paths a worker may import when dispatching a job.

Each is documented below with concrete configuration examples.

---

## 1. Dashboard Authentication (SEC-01)

The standalone FastAPI dashboard ships with an authenticating middleware that
runs in one of three modes.

### Modes

| Mode                  | Trigger                                                                                       | Behavior                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `standalone` (default) | top-level ASGI app, no env override                                                           | Every request must carry the `X-Sqlery-Key` header. Unauthenticated requests get `401 {"detail":"unauthorized"}`. |
| `disabled`            | `SQLERY_DASHBOARD_AUTH=disabled`                                                              | Pass-through. A single `WARNING` is logged at install time.                                           |
| `inherit` (explicit)  | `SQLERY_DASHBOARD_AUTH=inherit`                                                               | Pass-through. The parent application owns authentication.                                             |
| `inherit` (automatic) | Sqlery is mounted under a parent ASGI app (`scope["root_path"]` is non-empty at request time) | Pass-through. Detected via Starlette's request scope, regardless of how the parent mounts the sub-app. |

**Precedence:** an explicit `SQLERY_DASHBOARD_AUTH` env value always wins. If
you mount Sqlery inside a parent app but explicitly set
`SQLERY_DASHBOARD_AUTH=standalone`, Sqlery will enforce its own auth on top of
whatever the parent does.

### Key Resolution Order

Sqlery resolves the dashboard API key on startup in this order:

1. **`SQLERY_DASHBOARD_API_KEY` environment variable** — highest priority.
2. **`./.sqlery/dashboard.key`** file relative to the working directory.
3. **Auto-generated** 32-byte URL-safe token via `secrets.token_urlsafe(32)`.
   The generated key is persisted to `./.sqlery/dashboard.key` and printed
   **once** to stderr at startup. Capture it from the logs and store it
   somewhere durable.

### File Permissions

When Sqlery creates the key file itself, it uses strict permissions:

| Path                   | Mode    |
| ---------------------- | ------- |
| `./.sqlery/`           | `0o700` |
| `./.sqlery/dashboard.key` | `0o600` |

The file is written with `umask 0o077` and an explicit `os.chmod()`
belt-and-suspenders. If you provision the file yourself, match these
permissions or your operating system / SELinux policy may refuse the read.

### Authentication Header

```http
GET /api/stats HTTP/1.1
Host: dashboard.example.com
X-Sqlery-Key: <your-api-key>
```

The header is case-insensitive (Sqlery uses Starlette's normalized header
accessor). Comparison uses `hmac.compare_digest` for constant-time equality.

### Unauthenticated Endpoint

`GET /healthz` is **always reachable**, in every mode, regardless of the
configured key. This is implemented inside the middleware (not by route
ordering) so load balancers, Kubernetes liveness probes, and orchestrators can
verify the process is alive without a credential.

### Worked Examples

**Production with a rotating key:**

```bash
export SQLERY_DASHBOARD_API_KEY="$(vault kv get -field=value secret/sqlery/dashboard)"
sqlery-web --host 0.0.0.0 --port 8080
```

**Local development, auth turned off:**

```bash
export SQLERY_DASHBOARD_AUTH=disabled
sqlery-web --reload
# stderr: WARNING: dashboard auth disabled via SQLERY_DASHBOARD_AUTH
```

**Embedded as a sub-application of a parent FastAPI app:**

```python
from fastapi import FastAPI
from starlette.routing import Mount
from sqlery.fastapi_sqlery.app import app as sqlery_app

parent = FastAPI()
# Your existing parent-app auth (cookie session, OAuth, ...) applies here.
parent.mount("/internal/sqlery", sqlery_app)
```

Sqlery sees the non-empty `scope["root_path"]` on each request and skips its
own auth — the parent's middleware stack runs first and is the single point of
authentication enforcement. If you want Sqlery to **also** enforce its own
key on top of the parent's auth, set `SQLERY_DASHBOARD_AUTH=standalone`
explicitly.

---

## 2. Webhook SSRF Protection (SEC-02)

When jobs complete (or fail), Sqlery can POST a notification to a configured
webhook URL. Without protection, that URL is operator- or job-author-controlled
and can be aimed at internal services — including the cloud-instance metadata
endpoint, which on most providers grants credentials.

Sqlery's webhook subsystem now validates every URL before it makes the request.

### What's Blocked

The denylist is enumerated in `src/sqlery/security/ssrf.py` and is enforced for
**every** address returned by `socket.getaddrinfo`. A URL is rejected if *any*
resolved address falls in:

**IPv4 networks:**

| Range            | Why                                                  |
| ---------------- | ---------------------------------------------------- |
| `127.0.0.0/8`    | Loopback                                             |
| `10.0.0.0/8`     | RFC 1918 private                                     |
| `172.16.0.0/12`  | RFC 1918 private                                     |
| `192.168.0.0/16` | RFC 1918 private                                     |
| `169.254.0.0/16` | Link-local (incl. AWS/GCP/Azure metadata `169.254.169.254`) |
| `0.0.0.0/8`      | "This network" / unspecified                         |
| `100.64.0.0/10`  | Carrier-grade NAT (CGNAT)                            |

**IPv6 networks:**

| Range      | Why                                                                |
| ---------- | ------------------------------------------------------------------ |
| `::1/128`  | Loopback                                                           |
| `::/128`   | Unspecified                                                        |
| `fe80::/10` | Link-local                                                         |
| `fc00::/7` | Unique-local (ULA), incl. AWS IPv6 metadata `fd00:ec2::254`        |

**Hostnames** (matched case-insensitively, pre-DNS):

- `localhost`
- `metadata`
- `metadata.google.internal`

**URL schemes** (allowlist, not denylist): only `http` and `https` are
permitted. `file://`, `gopher://`, `javascript:`, `ftp://`, and `ldap://`
are rejected at function entry.

### How It Works

`validate_webhook_url(url)` performs four stages:

1. **Scheme allowlist** — reject non-HTTP(S) URLs.
2. **Hostname denylist** — reject magic names before any DNS work.
3. **Literal IP check** — if the host is already an IP literal, check it
   against the denylist directly.
4. **DNS resolution** — call `socket.getaddrinfo(host, None)`, iterate every
   returned address family, and reject the URL if **any** result is in a
   blocked range (any-match denial).

A `WebhookURLBlocked` exception is raised. It subclasses `ValueError` so
existing broad `except` handlers in `send_webhook` catch it cleanly and convert
it to a `False` return + WARNING log tagged with the job id.

### Known Limitations (v1)

These are honestly disclosed so operators can make informed deployment
choices:

- **DNS-rebinding race (~50 ms window).** The validator calls `getaddrinfo`
  once. The underlying `requests` / `urllib3` HTTP stack then calls
  `getaddrinfo` *again* when it actually opens the TCP connection. A hostile
  authoritative DNS server can answer "public IP" on the first call and
  "RFC 1918 IP" on the second. v1 mitigates the common case (any-match denial
  during the validator's own lookup) but does not pin the resolved IP at the
  HTTP-adapter level. v2 work to pin via a custom `HTTPAdapter` is queued and
  out of scope for this release.

- **HTTP redirects are not re-validated.** `requests.post` follows redirects
  by default; if a tenant controls an external server that returns `302
  Location: http://10.0.0.5/...`, the second hop bypasses the gate.
  Hardening (`allow_redirects=False` or a redirect-aware adapter) is queued
  for v2.

- **IPv4-mapped IPv6 forms.** Addresses like `::ffff:10.0.0.1` are currently
  checked only against the IPv6 net list. A hostile resolver returning a
  mapped form of an RFC 1918 address may slip through. v2 will normalize via
  `ipaddress.IPv6Address.ipv4_mapped`.

### Overriding the Defaults

There is **intentionally no project-wide allowlist setting** to turn the
denylist off. We considered one and rejected it: a single env var that
disables SSRF protection is an irresistible foot-gun on the day someone is
debugging in production.

If you legitimately need webhooks to a private network — for example, a
self-hosted Slack-compatible webhook receiver on an internal IP — you have two
options:

1. **Per-callsite opt-in:** call `validate_webhook_url(url, allow_loopback=True)`
   directly. This permits *only* `127.0.0.0/8` and `::1`; all other private
   ranges (RFC 1918, link-local, ULA) remain blocked.

2. **Patch the denylist in your settings module:** mutate
   `sqlery.security.ssrf.BLOCKED_V4_NETS` (or `BLOCKED_V6_NETS`) in your own
   site-specific configuration code, so the change is reviewable in version
   control rather than driven by an env var.

```python
# in your site_settings.py, BEFORE workers start
import ipaddress
from sqlery.security import ssrf

# Allow webhooks to a specific internal /24 you trust.
ssrf.BLOCKED_V4_NETS = tuple(
    net for net in ssrf.BLOCKED_V4_NETS
    if not net.overlaps(ipaddress.ip_network("10.42.0.0/24"))
)
```

---

## 3. Django Admin CSRF (SEC-03)

Sqlery's Django integration exposes a JSON admin API for actions like
clearing queues, requeuing jobs, vacuuming, and manual intervention. These
endpoints are now protected by Django's standard `CsrfViewMiddleware`.

### What Changed

Phase 04 audited every state-changing endpoint and removed `@csrf_exempt` from
10 of them in `src/sqlery/django_sqlery/api_views.py`. POSTs now require the
`X-CSRFToken` header (or the `csrfmiddlewaretoken` form field) like any other
Django admin endpoint.

The protected endpoints (all in `api_views.py`):

1. `api_task_action`
2. `api_stop_job`
3. `api_worker_action`
4. `api_remove_queued_job`
5. `api_enqueue_job_now`
6. `api_job_priority`
7. `api_clear_jobs`
8. `api_archive_scheduled_jobs`
9. `api_vacuum`
10. `api_manual_intervention`

The dashboard's bundled JavaScript (`static/sqlery/js/dashboard.js`) already
reads the `csrftoken` cookie and sends it in `X-CSRFToken`, so no client-side
changes are required.

### Intentional Exemptions (3, in `views.py`)

Three `@csrf_exempt` decorators are kept on purpose because the views use
token authentication rather than cookie authentication. CSRF protects against
*cookie* abuse, so it does not apply when there are no auth cookies in play.

| View              | Why exempt is correct                                                                                       |
| ----------------- | ----------------------------------------------------------------------------------------------------------- |
| `internal_worker` | HMAC-authenticated via `X-Signature` + `X-Timestamp` headers with a 5-second timestamp expiry. No cookie path. |
| `health_check`    | Read-only liveness probe used by kubelets and load balancers. No state change, no auth.                     |
| `trigger_view`    | Envelope-HMAC authenticated via `core.triggers.handle`. No cookie path.                                     |

If you add a new state-changing admin endpoint, the default is that it inherits
CSRF protection automatically; only add `@csrf_exempt` if you have a
non-cookie auth mechanism in place and you document why here.

---

## 4. `ALLOWED_TASK_MODULES` (SEC-04)

By default, a worker will import any module path that an enqueued job names.
That is sometimes desirable (during development) and sometimes a problem
(in production, where a job-author with database write access can name *any*
module on `sys.path`).

`ALLOWED_TASK_MODULES` is an opt-in prefix allowlist that gates the import
before `importlib.import_module` runs.

### Default Behavior

**Unset = allow all.** This preserves backward compatibility — existing
deployments are not broken by upgrading.

### Configuration

**Django** (in `settings.py`):

```python
DJANGO_SQL_JOBS = {
    # ... other config ...
    "ALLOWED_TASK_MODULES": ["myapp.tasks", "myapp.jobs", "shared.workflows"],
}
```

**Standalone** (env var, comma-separated):

```bash
export SQLERY_ALLOWED_TASK_MODULES="myapp.tasks,myapp.jobs,shared.workflows"
```

Whitespace is stripped; empty entries are dropped; an all-empty value
collapses to "unset" (allow all).

### Match Semantics

Prefix match with a **dot boundary**. `["myapp"]` matches:

| Module path        | Allowed? |
| ------------------ | -------- |
| `myapp`            | yes (exact) |
| `myapp.tasks`      | yes (dot boundary) |
| `myapp.tasks.send` | yes (dot boundary) |
| `myapp_evil.tasks` | **no** (no dot boundary) |
| `myappextra`       | **no** |

This defends against the obvious bypass where an attacker names a sibling
module `myapp_evil` hoping the substring check passes.

When rejected, the worker raises `TaskModuleNotAllowed` (subclass of
`Exception`), the job is marked failed with the exception message, and
`importlib.import_module` is never reached.

### Production-Environment Warning

When `ALLOWED_TASK_MODULES` is unset and the worker startup environment
looks like production, Sqlery emits a single `WARNING` log line on the first
line of `WorkerProcess.run` (before the fork loop, so it fires exactly once
per worker run, not once per forked child).

The "looks like production" heuristic does a case-insensitive substring `prod`
scan across:

- `ENV`
- `ENVIRONMENT`
- `DJANGO_SETTINGS_MODULE`

To silence the warning: configure the option (even to a permissive value
like `["myapp"]`). The warning is advisory only; the worker continues to
operate.

---

## 5. Dead-Code Retention Policy

Sqlery uses a **mark-and-date** pattern for retiring code instead of deleting
it outright. Backward-compatibility shims, superseded modules, and large
commented-out blocks carry a marker in this form:

```python
# #CLEANUP 2026-05-14: <reason this exists>. Remove after 2027-05-14.
```

### Policy Rules

- **Default retention is 12 months** from the marker date. The `Remove after`
  date is always `<marker-date> + 12 months`.
- **Deletion is a separate decision**, not automatic. When the `Remove after`
  date arrives, a quarterly cleanup pass evaluates each marker individually:
  if no external consumer has reported breakage and the replacement code is
  stable, the marked block is deleted.
- **No silent deletions.** Removing a marked file is a separate, reviewable
  commit, not bundled with an unrelated change.

### Why

Sqlery's public API includes a number of import paths that downstream code
may depend on (`from sqlery import enqueue`, `from sqlery.webhooks import
send_webhook`, and similar). Renaming or moving these files without a grace
period would silently break external code on upgrade. The mark-and-date
pattern gives operators a 12-month window to:

1. See the deprecation in their own code review when they look at the file.
2. Migrate to the canonical path.
3. Catch up *before* the deletion lands.

### Where Markers Live

| File / area                                        | Reason                                              |
| -------------------------------------------------- | --------------------------------------------------- |
| `src/sqlery/webhooks.py`                           | BC stub — canonical is `sqlery/django_sqlery/webhooks.py` |
| `src/sqlery/{admin,apps,cleanup,...}.py` (20 files) | BC stubs — re-export from `sqlery/django_sqlery/...` |
| `src/sqlery/async_worker.py`                       | superseded; marker date is earlier (2026-11-14)     |
| `src/sqlery/core/daemon.py`                        | commented-out blocks (dead control flow)            |
| `src/sqlery/rate_limit_utils.py`                   | commented-out blocks                                |
| `src/sqlery/django_sqlery/utils.py`                | commented-out blocks                                |
| `src/sqlery/core/worker.py`                        | commented-out blocks                                |

To find every marked block in the tree:

```bash
grep -rn "Remove after" src/sqlery
```

---

## Reporting Security Issues

Please report security issues **privately**, not via a public GitHub issue.

The preferred channel is a GitHub Security Advisory (Repository → Security →
Advisories → "Report a vulnerability"), which keeps the report private to the
maintainers until a fix is ready. If that is unavailable to you, email the
maintainer at the address listed in `pyproject.toml`.

We aim to acknowledge security reports within 72 hours and to issue a patch
release within 14 days for vulnerabilities that affect a default
configuration.
