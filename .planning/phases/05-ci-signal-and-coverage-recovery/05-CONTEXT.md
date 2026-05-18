# Phase 5: CI Signal and Coverage Recovery - Context

**Gathered:** 2026-05-15
**Status:** Ready for planning
**Source:** Inline user guidance during `$gsd-plan-phase 5`

<domain>
## Phase Boundary

Phase 5 is a maturity pass on the existing test/CI system. It should fix misleading or low-signal quality gates without growing the codebase much. The point is to make the current proof machinery trustworthy, not to add product surface area.

</domain>

<decisions>
## Implementation Decisions

### Locked decisions

- Keep scope tight. Prefer quality-bar improvements over feature work.
- Do not grow the codebase much just to satisfy coverage optics.
- Raise the quality bar using targeted fixes, cleanup, and stronger signal.
- Reduce slop: remove misleading tolerances, fake-green paths, and flaky/low-value debt where possible.
- Favor simple, auditable CI and test mechanics over clever abstractions.

### The agent's Discretion

- Exact split between test fixes, CI workflow changes, and coverage-config changes.
- Whether a broken/legacy test should be repaired, isolated, or explicitly deferred with a documented stub.
- The precise ratcheting strategy for the coverage gate, as long as it is honest and materially stronger than the current `13%` floor.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone and phase scope
- `.planning/ROADMAP.md` - active Phase 5 goal, success criteria, and plan slots
- `.planning/REQUIREMENTS.md` - TEST-01..04 requirements for this phase
- `.planning/PROJECT.md` - milestone-level maturity-first direction

### Prior audit evidence
- `.planning/phases/03-testing-ci/03-VERIFICATION.md` - concrete gaps in Phase 3 test/CI truthfulness
- `.planning/v0.21-MILESTONE-AUDIT.md` - cross-phase audit, deferred CI/coverage follow-ups, standalone-no-Django confidence gap

### Live implementation targets
- `pyproject.toml` - coverage config and pytest markers
- `.github/workflows/test.yml` - CI rails, coverage step, standalone-no-Django job
- `tests/unit/test_django_backend.py` - previously failing Django-backend tests
- `tests/unit/test_worker.py` - fork-lifecycle regression noted in prior verification
- `tests/chaos/test_property_based.py` - collection-debt hotspot
- `tests/test_core_standalone.py` - standalone-no-Django proof target

</canonical_refs>

<specifics>
## Specific Ideas

- Start with the smallest fixes that make CI honest again.
- Prefer deleting or isolating noisy test debt over adding broad new scaffolding.
- Coverage should become a trusted ratchet, not a vanity number.

</specifics>

<deferred>
## Deferred Ideas

- Compat expansion stays deferred to the next milestone.
- Broader battle-testing and PostgreSQL contention work belong to Phase 6.

</deferred>

---

*Phase: 05-ci-signal-and-coverage-recovery*
*Context gathered: 2026-05-15 via inline planning discussion*
