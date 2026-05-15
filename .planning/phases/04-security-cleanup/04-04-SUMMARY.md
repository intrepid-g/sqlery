---
phase: 04-security-cleanup
plan: 04
subsystem: security
tags: [security, ssrf, webhooks, network]
requirements: [SEC-02]
dependency_graph:
  requires: []
  provides: [sqlery.security.ssrf.validate_webhook_url, sqlery.security.ssrf.WebhookURLBlocked]
  affects: [src/sqlery/webhooks.py]
tech_stack:
  added: []
  patterns: [resolve-then-check, any-match-denial, scheme-allowlist]
key_files:
  created:
    - src/sqlery/security/__init__.py
    - src/sqlery/security/ssrf.py
    - tests/unit/__init__.py
    - tests/unit/test_ssrf.py
  modified:
    - src/sqlery/webhooks.py
decisions:
  - "Block at validation time via socket.getaddrinfo + any-match denial; accept ~50ms DNS-rebinding window as documented v1 limitation."
  - "WebhookURLBlocked subclasses ValueError so existing broad excepts catch it; send_webhook converts to False return."
  - "Hostname denylist (localhost, metadata.google.internal) enforced pre-DNS to short-circuit before any resolver contact."
  - "Scheme allowlist (http/https only) blocks file://, gopher://, javascript:, ftp://, ldap:// at function entry."
metrics:
  duration_minutes: ~10
  tasks_completed: 2
  files_created: 4
  files_modified: 1
  tests_added: 49
completed: 2026-05-15
---

# Phase 04 Plan 04: SEC-02 Webhook SSRF Defense Summary

SSRF guard for outbound webhooks: resolve-then-check denylist module
(`sqlery.security.ssrf`) wired into `send_webhook` so any URL targeting
loopback, RFC1918, link-local (incl. AWS/GCP metadata), IPv6 ULA, CGNAT,
or unspecified ranges is rejected before HTTP egress.

## What Was Built

### `sqlery.security.ssrf` (new module)

- `WebhookURLBlocked(ValueError)` — single exception type; ValueError
  subclass so callers' broad excepts (and the existing
  `except Exception` around `requests.post`) handle it cleanly.
- `validate_webhook_url(url, *, allow_loopback=False) -> None` performs
  4 stages:
  1. **Scheme allowlist** — only `http`/`https`; blocks `file://`,
     `gopher://`, `javascript:`, `ftp://`, `ldap://`, etc.
  2. **Hostname denylist (pre-DNS)** — case-insensitive match against
     `{localhost, metadata.google.internal, metadata}`.
  3. **Literal IP check** — if hostname parses as an IPv4/IPv6 literal,
     check it directly against `BLOCKED_V4_NETS` / `BLOCKED_V6_NETS`.
  4. **DNS resolution + ALL-IPs check** — `socket.getaddrinfo(host, None)`
     returns all families; we iterate every tuple, parse `sockaddr[0]`
     to `ipaddress.ip_address`, and reject the URL if **any** result is
     in a blocked range (any-match denial). `gaierror` (NXDOMAIN, etc.)
     is re-raised as `WebhookURLBlocked("dns failure: ...")`.
- Denylist constants (module-level, frozen):
  - V4: `127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`,
    `0.0.0.0/8`, `100.64.0.0/10`
  - V6: `::1/128`, `::/128`, `fe80::/10`, `fc00::/7`
- `allow_loopback=True` opt-in permits **only** `127/8` and `::1`; all
  other ranges (RFC1918, link-local metadata, etc.) remain blocked.

### `send_webhook` integration (`src/sqlery/webhooks.py`)

Inserted the SSRF gate at the top of `send_webhook`, after the
`webhook_url`/`webhook_events` short-circuits but **before** the optional
`import requests` and before any HTTP setup. Caught `WebhookURLBlocked`
locally → log a WARNING tagged with `job.id` and the reason → `return False`.
The False return is the existing failure contract; the Django model
`mark_success`/`mark_failed` callers never see an exception.

`send_webhook_with_retry` was intentionally left untouched — every retry
goes through `send_webhook`, so each attempt re-validates (re-resolves
DNS), which is the correct semantics for the any-match defense even
when an authoritative resolver flips answers between attempts.

## Test Coverage (`tests/unit/test_ssrf.py`, 49 tests, all passing)

- Subclass relationship: `WebhookURLBlocked` is a `ValueError`.
- Scheme rejection: `file://`, `gopher://`, `ftp://`, `javascript:`, `ldap://`.
- Missing-host rejection.
- Hostname denylist: `localhost`/`LOCALHOST`, `metadata.google.internal`
  (case-insensitive), `metadata`.
- Literal IPv4 denylist: 13 parametrized cases spanning every
  `BLOCKED_V4_NETS` entry (including range boundaries: `127.255.255.254`,
  `10.255.255.1`, `172.31.255.1`, `100.127.255.1`).
- Literal IPv6 denylist: 6 cases incl. `::1`, `::`, `fe80::1`, `fc00::1`,
  and the AWS-v6-metadata ULA `fd00:ec2::254`.
- **DNS rebinding** (any-match denial):
  - public-then-private (`1.2.3.4` + `10.0.0.5`) → reject
  - private-then-public (order independence) → reject
  - mixed v4+v6 (`1.2.3.4` + `fc00::1`) → reject
- DNS failure (`gaierror`) → `WebhookURLBlocked("dns failure: ...")`.
- Happy path: public URL with mocked `getaddrinfo` → no raise; raw public
  literal `8.8.8.8` → no raise; non-default port `:8443` → no raise.
- `allow_loopback=True`: permits `127.0.0.1` and `[::1]`; still blocks
  `10.0.0.1`, `metadata.google.internal`, `169.254.169.254`.
- Constant-coverage sanity tests: every `BLOCKED_V4_NETS` /
  `BLOCKED_V6_NETS` entry is enumerated, forcing new entries to update
  the test set.
- `send_webhook` integration: blocked loopback → False + WARNING log;
  blocked metadata IP → False; empty `webhook_url` short-circuit
  unchanged.

All DNS is monkeypatched via `monkeypatch.setattr(socket, "getaddrinfo", …)`
— no real network access in the test suite.

```
49 passed in 0.03s
```

## Known Limitations (documented v1)

- **DNS-rebinding race (~50 ms window).** Our validation calls
  `getaddrinfo` once; the underlying `requests`/`urllib3` HTTP client
  then calls it again at connect time. A malicious authoritative
  resolver can serve a benign answer to the validator and a private
  answer to the connector. Mitigated for the common case (any-match
  denial during the validator's own resolution) but not eliminated. v2
  hardening (pinning the resolved IP via a custom `HTTPAdapter` that
  connects directly to the validated address) is **out of scope** for
  this plan and is queued for 04-06 SECURITY.md follow-up tracking.
- **HTTP redirects.** `requests.post` follows redirects by default; the
  redirect target is NOT re-validated by this gate. If a tenant
  controls an external server that 302s into RFC1918, the second hop
  bypasses SSRF. Hardening (set `allow_redirects=False` or wrap
  redirects with a custom adapter that re-validates) is also queued for
  v2.
- **IPv4-mapped IPv6 (`::ffff:10.0.0.1`).** Currently checked only
  against V6 nets; a hostile resolver returning a mapped form of a
  RFC1918 address may slip through. v2: normalize via
  `IPv6Address.ipv4_mapped`. Tracked as residual risk.

## Deviations from Plan

### Rule 3 — Blocking issue: webhook file location

- **Found during:** Task 2.
- **Issue:** Plan instructed to wire the SSRF check into
  `src/sqlery/django_sqlery/webhooks.py` (the post-04-01 location). In
  this worktree base, plan 04-01 has not been applied; the canonical
  file is still `src/sqlery/webhooks.py` and `django_sqlery/webhooks.py`
  does not exist.
- **Fix:** Wired into `src/sqlery/webhooks.py` (the canonical location
  in this branch) using the absolute import
  `from sqlery.security.ssrf import ...`. When 04-01 lands and the file
  moves to `django_sqlery/`, the import and the inserted block carry
  over verbatim — no further changes needed.
- **Files modified:** `src/sqlery/webhooks.py`.
- **Commit:** `1b887eb`.

### Rule 3 — Blocking issue: missing `tests/unit/` directory

- **Found during:** Task 1.
- **Issue:** Plan specifies `tests/unit/test_ssrf.py` but no `tests/unit/`
  directory exists in this base.
- **Fix:** Created `tests/unit/__init__.py` + `tests/unit/test_ssrf.py`.
- **Commit:** `37cacb6`.

## Commits

| Task | Hash | Message |
|------|------|---------|
| 1 | `37cacb6` | feat(04-04): add SSRF defense module sqlery.security.ssrf |
| 2 | `1b887eb` | feat(04-04): wire SSRF validation into send_webhook (SEC-02) |

## Self-Check: PASSED

- `src/sqlery/security/__init__.py` — FOUND
- `src/sqlery/security/ssrf.py` — FOUND (≥70 lines: 137 lines)
- `tests/unit/test_ssrf.py` — FOUND (≥120 lines: 247 lines)
- `src/sqlery/webhooks.py` — MODIFIED (validate_webhook_url called)
- Commit `37cacb6` — FOUND
- Commit `1b887eb` — FOUND
- 49/49 tests passing
