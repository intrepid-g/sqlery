---
phase: 04-security-cleanup
plan: 06
subsystem: docs
tags: [docs, security, operator-facing]
requirements: [SEC-01, SEC-02, SEC-03, SEC-04, CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-04]
dependency_graph:
  requires: [04-02, 04-03, 04-04, 04-05]
  provides: [docs/SECURITY.md, README Security link]
  affects: [README.md]
tech_stack:
  added: []
  patterns: [operator-facing reference doc]
key_files:
  created:
    - docs/SECURITY.md
  modified:
    - README.md
decisions:
  - "Single doc covers all four SEC controls + dead-code policy rather than four small docs; operators read security top-down."
  - "Honestly disclosed DNS-rebinding ~50ms window and redirect re-validation gap as v1 limitations rather than burying them."
  - "Documented intentionally NO project-wide SSRF allowlist env var (foot-gun); override pattern is per-callsite or in-code patch of BLOCKED_V4_NETS."
metrics:
  duration_minutes: ~10
  tasks_completed: 2
  files_created: 1
  files_modified: 1
completed: 2026-05-15
---

# Phase 04 Plan 06: Operator-Facing Security Documentation Summary

Final plan of Phase 04. Writes `docs/SECURITY.md` covering the four security
controls landed in plans 04-02 through 04-05 (`SEC-01`..`SEC-04`) plus the
dead-code retention policy established in plan 04-01 (`CLEAN-01`..`CLEAN-04`),
and links it from `README.md`.

## What Was Built

### `docs/SECURITY.md` (new, 444 lines)

Seven-section operator-facing reference document:

1. **Overview** — threat model statement (worker is trusted; dashboard +
   webhooks + admin POSTs + task imports are the hardening surfaces).
2. **Dashboard Authentication (SEC-01)** — three modes table, key resolution
   order, file permissions (`0o700` / `0o600`), `X-Sqlery-Key` header,
   `/healthz` bypass, mount auto-detect via `scope["root_path"]`, three
   worked examples (prod, local-disabled, embedded sub-app).
3. **Webhook SSRF Protection (SEC-02)** — full IPv4/IPv6 denylist tables,
   scheme allowlist, four-stage validation flow, three honestly-disclosed v1
   limitations (DNS-rebinding ~50ms window, no redirect re-validation,
   IPv4-mapped IPv6), and the per-callsite override pattern (no global
   allowlist env var).
4. **Django Admin CSRF (SEC-03)** — list of the 10 protected endpoints in
   `api_views.py` and the 3 intentional exemptions in `views.py`
   (`internal_worker` HMAC, `health_check` read-only, `trigger_view`
   envelope-HMAC) with the reason each exemption is correct.
5. **`ALLOWED_TASK_MODULES` (SEC-04)** — Django and standalone config syntax,
   dot-boundary prefix-match semantics with examples table
   (`myapp_evil.tasks` rejected), production-env warning heuristic.
6. **Dead-code retention policy** — mark-and-date pattern, 12-month default
   retention, quarterly deletion decision, grep recipe for finding markers.
7. **Reporting security issues** — GitHub Security Advisory channel.

### `README.md` modification

Added a `## 🔒 Security` section between Documentation and Use Cases pointing
to `docs/SECURITY.md`. Single-purpose edit; no other README changes.

## Commits

| Hash      | Message |
| --------- | ------- |
| `9dc92ed` | docs(04-06): add SECURITY.md covering SEC-01..04 + dead-code policy |

(Single commit covers both Task 1 and Task 2 — they are a logically atomic
change: the doc is useless without the README link, and the README link is
useless without the doc.)

## Verification

```text
wc -l docs/SECURITY.md        → 444  (min required: 120)
grep -c SQLERY_DASHBOARD_AUTH docs/SECURITY.md          → ≥1 ✓
grep -c ALLOWED_TASK_MODULES docs/SECURITY.md           → ≥1 ✓
grep -c X-Sqlery-Key docs/SECURITY.md                   → ≥1 ✓
grep -c "Remove after" docs/SECURITY.md                 → ≥1 ✓
grep -c "DNS-rebinding\|DNS rebinding" docs/SECURITY.md → ≥1 ✓
grep -q docs/SECURITY.md README.md                      → match ✓
```

All seven required sections present. Each SEC control has at least one
concrete env-var-with-value example. All three intentional `@csrf_exempt`
exemptions named with reasons. DNS-rebinding limitation called out in a
dedicated "Known Limitations" subsection (not buried).

## Deviations from Plan

**[Plan-instruction] Phase-3 checkpoint (Task 3 human review) converted to
automated verification.** Per the parallel-executor mode prompt, the
`checkpoint:human-verify` gate is replaced by inline automated grep checks
documented above. The doc is ready for human review on the PR; the
checkpoint protocol is satisfied by the grep contract in the plan's
`<verify><automated>` block, which passes.

**[Rule 3 — Blocker] `.planning/phases/04-security-cleanup/` did not exist
in the worktree.** The worktree was branched from an older base than the
main repo's current planning state. Source material (04-01..04-05 SUMMARY
files, the 04-06 PLAN, CONTEXT, RESEARCH) was read from the main-repo path
(`/Users/user/Documents/GitHub/sqlery/.planning/...`) instead of the
worktree path. Created the worktree's `.planning/phases/04-security-cleanup/`
directory for the new SUMMARY commit.

## Known Stubs

None. This plan is documentation-only.

## Threat Flags

None. No new attack surface introduced — the doc describes existing
controls.

## Self-Check: PASSED

- `docs/SECURITY.md` — FOUND (444 lines, ≥120 required)
- `README.md` contains `docs/SECURITY.md` link — confirmed via `grep -q`
- Commit `9dc92ed` — FOUND in `git log`
- All seven required substrings present (`SQLERY_DASHBOARD_AUTH`,
  `ALLOWED_TASK_MODULES`, `X-Sqlery-Key`, `Remove after`, `DNS-rebinding`,
  `internal_worker`, `health_check`)
- `pyproject.toml` — UNCHANGED
- `STATE.md` / `ROADMAP.md` — NOT MODIFIED (per parallel-executor prompt)
