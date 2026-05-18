# Phase 5 Research: CI Signal and Coverage Recovery

**Phase:** 5 - CI Signal and Coverage Recovery
**Date:** 2026-05-15
**Question:** What do we need to know to plan this phase well?

## Research Summary

Phase 5 should not try to "win coverage" by adding large amounts of new code or broad new suites. The highest-leverage work is to restore honesty:

1. remove known collection and test-failure debt that makes CI red for the wrong reasons
2. replace the fake-low `13%` coverage floor with a defensible ratchet based on a clean collected suite
3. close the remaining standalone-no-Django confidence gap so the CI job proves something real instead of waiting on a human checkbox

This is a cleanup-and-proof phase, not a feature phase.

## Evidence from existing artifacts

### From `03-VERIFICATION.md`

Concrete Phase 3 gaps already documented:

- `tests/unit/test_django_backend.py`
  - two tests were identified as failing under Django DB access rules during verification
- `tests/unit/test_worker.py`
  - one fork-lifecycle test was identified as failing under Django DB access rules during verification
- `tests/chaos/test_property_based.py`
  - collection-time import debt was severe enough to break the postgres CI rail
- `pyproject.toml`
  - coverage gate pinned at `fail_under = 13`
- `.github/workflows/test.yml`
  - postgres rail was vulnerable to failing on collection noise rather than meaningful regressions

### From `v0.21-MILESTONE-AUDIT.md`

The audit reframed some of the above:

- the failing Django-backend tests were ultimately tied to a real production arity bug that got fixed later
- `tests/chaos/test_property_based.py` was intentionally stubbed with a module-level skip to keep collection clean pending rewrite
- `fail_under = 13` remained as an accepted temporary floor, explicitly routed to backlog
- the `standalone-no-django` job exists, but the confidence claim still depends on a human green-observation item

Implication: Phase 5 must re-check current live state instead of blindly trusting the first verifier or the final audit narrative. The plan should begin with measurement and evidence gathering before tightening gates.

## Practical planning guidance

### 1. Keep code growth low

Do not respond to low confidence by adding wide new harnesses. Prefer:

- fixing or isolating the few tests that distort truth
- removing stale tolerances/comments
- tightening existing rails
- documenting exactly what each rail proves

### 2. Coverage must be honest before it is high

The wrong move is jumping straight back to `70%` because an older plan said so.

The right order is:

1. clean collection
2. re-measure the real baseline
3. raise the gate to a defensible number
4. document the next ratchet step if the baseline still falls short of the aspirational target

That aligns with the user's "raise the bar, reduce the slop" instruction better than a pretend threshold.

### 3. Standalone-no-Django proof should be automated

The repo already has:

- `tests/test_core_standalone.py`
- a `standalone-no-django` CI job in `.github/workflows/test.yml`

Phase 5 should convert the remaining trust gap from "someone saw green once" into a replayable automated proof. That likely means making the CI job run an explicit import/test sequence whose output is unambiguous in logs.

### 4. Avoid broadening this phase into battle-testing

Phase 6 already owns:

- crash/recovery hardening
- zombie/heartbeat/lease behavior
- PostgreSQL concurrency pressure tests

Phase 5 should stop once CI, coverage, and standalone-no-Django proof are honest and maintainable.

## Recommended plan split

### Plan 05-01

Fix collection/test debt and restore clean execution of the current default and postgres rails.

### Plan 05-02

Re-measure coverage from a clean suite and replace the emergency `13%` floor with a ratcheted, defensible gate plus clear CI reporting.

### Plan 05-03

Turn the standalone-no-Django confidence gap into an automated CI proof and remove the lingering human-verification dependency.

## Risks

- over-scoping into general test expansion instead of signal repair
- reintroducing brittle CI checks that fail on collection noise
- gaming coverage numerically instead of improving trust
- making the standalone-no-Django job appear meaningful without actually asserting the import/runtime contract

## Conclusion

The 80/20 move is not more tests everywhere. It is making the existing quality machinery truthful, smaller, and harder to fool.

That means:

- fix the few red herrings
- tighten the rails
- make the gate honest
- automate the one remaining confidence hole

---

*Research complete: 2026-05-15*
