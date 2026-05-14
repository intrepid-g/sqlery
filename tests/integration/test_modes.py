"""Parametrized E2E tests for the existing execution modes.

This test module covers the six (mode, integration, db) cells that DO NOT
require any net-new mode implementation. The remaining four cells
(subprocess-standalone, http-trigger-standalone, lambda × 2, async × 2)
ship in Plan 02-08 and are skipped here.

Cells under test (SQLite, fast):
- (daemon, django)
- (daemon, standalone)
- (subprocess, django)
- (http-trigger, django)
- (sync, django)
- (sync, standalone)

The same six cells are parametrized against Postgres and marked
``postgres`` (plan 03-07, TEST-11) so they ride the dedicated PG CI rail
and are skipped on SQLite rails. The ``postgres`` rows also auto-skip
when ``SQLERY_TEST_PG_URL`` is unset (see ``conftest.pytest_collection_modifyitems``).

Requirements satisfied: DMOD-01 (daemon-django), DMOD-02 (subprocess-django),
DMOD-03 (http-trigger-django), DMOD-05 (sync-django), SMOD-01
(daemon-standalone), SMOD-05 (sync-standalone).
"""

from __future__ import annotations

import pytest


# Mode × integration matrix. Cells deferred to Plan 02-08 are still included
# here so the matrix shape is identical to the one 02-08 will land against;
# `pytest_collection_modifyitems` in conftest.py applies the skip marker.
MODES = ["daemon", "subprocess", "http-trigger", "sync"]
INTEGRATIONS = ["django", "standalone"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("integration", INTEGRATIONS)
@pytest.mark.parametrize(
    "db",
    [
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ],
)
def test_mode_e2e(mode, integration, db, harness):
    """Enqueue -> drive mode to completion -> assert success + result.

    The four deferred-to-02-08 cells (subprocess-standalone,
    http-trigger-standalone) get a ``skip("covered by plan 02-08")`` marker
    applied at collection time via ``conftest.pytest_collection_modifyitems``.
    Postgres rows are skipped automatically when ``SQLERY_TEST_PG_URL`` is
    unset.
    """
    job_id = harness.enqueue(a=1, b=2)
    harness.run_mode_until_finished(job_id, timeout=30)

    status = harness.status(job_id)
    assert status == "success", (
        f"mode={mode} integration={integration} db={db}: "
        f"expected status 'success' but got {status!r}"
    )

    result = harness.result(job_id)
    assert result == 3, (
        f"mode={mode} integration={integration} db={db}: "
        f"expected result 3 but got {result!r}"
    )
