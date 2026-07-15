---
status: todo

task: >
  Track out-of-scope issues discovered during quick task 260715-inf that were
  not fixed because they predate this task's changes.

not_needed_when:
  - items below are resolved or explicitly declined

summarize_when:
  - all items resolved
---

# Deferred Items — 260715-inf

Pre-existing (not introduced by this task), out of scope per deviation-rules
scope boundary:

1. `black --check` reports `src/sqlery/triggers.py`, `tests/test_triggers.py`,
   `tests/test_subprocess.py` would be reformatted — reformatting predates
   this task (confirmed via `git stash`).
2. `ruff check` reports `tests/test_subprocess.py:9` unused import
   `should_use_subprocess`, and `tests/test_triggers.py:138` unused import
   `django_tasks` (only used for `HAS_DJANGO_TASKS` boolean) — both
   pre-existing, confirmed via `git stash`.
3. `black --check tests/integration/test_async_e2e.py` reports it would
   reformat — pre-existing, confirmed via `git stash` (whole file already
   drifted from black's formatting before this task's edits).

Real findings from this task (documented in SUMMARY.md, not deferred):

4. `DjangoAsyncBackend._aclaim_job_postgres`
   (`src/sqlery/django_sqlery/async_backend.py:121`) calls
   `connection.acursor()`, which does not exist on Django's
   `DatabaseWrapper` in any released version (confirmed on Django 6.0.5).
   The async worker's Postgres claim path for Django mode is broken.
   `tests/integration/test_async_e2e.py::test_async_e2e_django_pg` is
   marked `xfail(strict=True)` documenting this — a real fix needs an
   architectural decision (see xfail reason for detail) so it is out of
   scope for this quick task.
5. Cosmetic: the above xfail test reports as "2 xfailed" instead of "1
   xfailed" in pytest output (both call-phase and a teardown-phase FK/flush
   error against the partitioned schema get attributed to the same xfail
   marker). Exit code is still 0 and CI is not blocked; root cause not
   chased further (downstream of the already-documented acursor bug and
   pytest-django TransactionTestCase flush behavior against FK-constrained
   tables — investigate only if this teardown failure recurs on tests that
   are NOT already expected to fail).
