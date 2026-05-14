---
phase: 03-testing-ci
plan: 05
subsystem: webhooks
tags: [unit-tests, webhooks, hmac, retry, http-mock]
requires: []
provides:
  - "Direct unit coverage of src/sqlery/webhooks.py (TEST-10)"
affects: []
tech-stack:
  added: []
  patterns: ["stdlib unittest.mock only", "MagicMock for QueuedJob row stand-ins"]
key-files:
  created:
    - tests/unit/__init__.py
    - tests/unit/test_webhooks.py
  modified: []
decisions:
  - "Used unittest.mock (stdlib) — pytest-mock is intentionally not a project dep."
  - "Mocked HTTP via patch.object(webhooks_mod, 'requests') — `requests` is an undeclared optional dep (Phase 4 CLEAN-04)."
  - "Stubbed sqlery.settings.get_setting at import time — sqlery/settings.py is an empty backward-compat shim (Phase 4 cleanup item)."
  - "SSRF check left as @pytest.mark.xfail (handoff to SEC-02 in Phase 4)."
metrics:
  duration: "~10 minutes"
  completed: "2026-05-14"
  tests_added: 38
  tests_passing: 37
  tests_xfailed: 1
  module_coverage: "100% of src/sqlery/webhooks.py"
---

# Phase 03 Plan 05: Webhooks Unit Tests Summary

One-liner: HMAC + retry + HTTP-delivery unit tests for `src/sqlery/webhooks.py` using stdlib `unittest.mock`, reaching 100% module coverage with zero network access.

## What Was Built

- `tests/unit/__init__.py` (new package marker so pytest can collect from `tests/unit/`).
- `tests/unit/test_webhooks.py` covering:
  - `TestHMACSigning` — determinism, pinned vector against a hand-computed digest (T-03-09), header naming (`X-Sqlery-Signature: sha256=<hex>`), empty-payload, secret-missing branches.
  - `TestHTTPDelivery` — 200/201/202/204 success paths; 4xx failure-no-retry; 5xx failure; `Timeout`; generic `RequestException`; generic `Exception`; module-load `requests=None` branch; empty-URL short-circuit.
  - `TestRetryBackoff` — counter increment, max-retry cap → `webhook_status='failed'`, first-attempt `pending` marker, success short-circuits the retry path. `time.sleep` is patched defensively for any future inline backoff.
  - `TestEventFiltering` — unsubscribed events silently skipped, subscribed events fire, empty events list skips all.
  - `TestPayloadShape` — documented JSON fields present (`event, job_id, task_path, status, queue_name, priority, created_at, started_at, finished_at, duration_seconds, output, error, retry_count, tags`); None timestamps survive; `User-Agent` and `Content-Type` headers correct.
  - `TestSafeEncoder` — every `_SafeEncoder.default` branch (UUID, datetime/date/time, timedelta, Decimal, set/frozenset, bytes) plus fall-through `TypeError`.
  - `TestRetryFailedWebhooks` — batch retry loop with patched `QueuedJob.objects` returning a mock queryset; counts success/failure/skipped correctly.
  - `test_ssrf_blocks_loopback_destinations` — `@pytest.mark.xfail` placeholder hand-off to SEC-02 (Phase 4).

## Verification

```
PYTHONPATH=. uv run pytest tests/unit/test_webhooks.py --cov=sqlery.webhooks --cov-report=term-missing --cov-fail-under=80
```

Result: **37 passed, 1 xfailed; 100% coverage of `src/sqlery/webhooks.py`** (107/107 stmts).

Network: zero real HTTP — `sqlery.webhooks.requests` is patched in every HTTP-touching test.

## Deviations from Plan

### [Rule 3 - Blocking] Stubbed `sqlery.settings.get_setting` at test-import time

- **Found during:** First test collection
- **Issue:** `sqlery/webhooks.py` does `from .settings import get_setting`, but `sqlery/settings.py` is an empty backward-compat shim that no longer re-exports `get_setting`. Module import fails before any test can run.
- **Fix:** In `tests/unit/test_webhooks.py` we install a no-op `get_setting` onto the empty `sqlery.settings` module *before* importing `sqlery.webhooks`. Production code is unchanged.
- **Why test-only:** Repairing the shim is a Phase 4 cleanup concern (matches the `# #CLEANUP:` marker in `sqlery/settings.py`); test scope is to cover behaviour, not to fix the import graph.
- **Files modified:** test file only.

No other deviations. Plan executed as written.

## Known Stubs

None introduced by this plan. The SSRF xfail explicitly flags a future hand-off rather than masking a stub.

## Threat Flags

None. All threats are already in the plan's `<threat_model>`:

- **T-03-09 (Tampering — HMAC scheme)** — mitigated by `test_signature_pins_known_vector`, which pins the algorithm against an independently-computed digest.
- **T-03-10 (Info disclosure — SSRF)** — transferred to SEC-02 (Phase 4), flagged by xfail.

## Coverage Notes

Single test file drives 107/107 statements in `src/sqlery/webhooks.py`. Branch coverage was not measured (project doesn't enable `--cov-branch` by default); switch on `--cov-branch` to track further if desired in Phase 4.

## Self-Check: PASSED

- `tests/unit/test_webhooks.py` — FOUND
- `tests/unit/__init__.py` — FOUND
- No `pytest_mock` import — VERIFIED (`grep` shows only docstring mention)
- Module coverage 100% ≥ 80% — VERIFIED
- 37 passed + 1 xfailed, 0 failed — VERIFIED
