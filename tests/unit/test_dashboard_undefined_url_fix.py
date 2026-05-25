"""
Regression test for the dashboard.js undefined-URL fix (REGRESSION 2026-05-25).

Validates that updateStats, updateTasks, and pollFeed all check that their
fetch URLs are strings before calling fetch, preventing requests to
/admin/sqlery/undefined when DASHBOARD_CONFIG is missing or incomplete.
"""

from pathlib import Path

import pytest

DASHBOARD_JS = Path(__file__).parent.parent.parent / "src" / "sqlery" / "django_sqlery" / "static" / "sqlery" / "js" / "dashboard.js"


@pytest.fixture(scope="module")
def dashboard_js() -> str:
    assert DASHBOARD_JS.exists(), f"dashboard.js not found at {DASHBOARD_JS}"
    return DASHBOARD_JS.read_text()


class TestUndefinedUrlFix:
    """Validate guard clauses that prevent fetch(undefined)."""

    def test_url_ok_helper_exists(self, dashboard_js: str):
        assert "function _urlOk(url)" in dashboard_js
        assert "typeof url === 'string'" in dashboard_js

    def test_updateStats_checks_url_before_fetch(self, dashboard_js: str):
        idx_guard = dashboard_js.find("if (!_urlOk(statsUrl))")
        idx_fetch = dashboard_js.find("const response = await fetch(statsUrl)")
        assert idx_guard > 0, "updateStats guard missing"
        assert idx_fetch > 0, "updateStats fetch missing"
        assert idx_guard < idx_fetch, "guard must come before fetch"

    def test_updateTasks_checks_url_before_fetch(self, dashboard_js: str):
        idx_guard = dashboard_js.find("if (!_urlOk(tasksUrl))")
        idx_fetch = dashboard_js.find("const response = await fetch(tasksUrl)")
        assert idx_guard > 0, "updateTasks guard missing"
        assert idx_fetch > 0, "updateTasks fetch missing"
        assert idx_guard < idx_fetch, "guard must come before fetch"

    def test_pollFeed_checks_url_before_fetch(self, dashboard_js: str):
        idx_guard = dashboard_js.find("if (!_urlOk(DASHBOARD_CONFIG.activityFeedUrl))")
        idx_fetch = dashboard_js.find("const resp = await fetch(url)")
        assert idx_guard > 0, "pollFeed guard missing"
        assert idx_fetch > 0, "pollFeed fetch missing"
        assert idx_guard < idx_fetch, "guard must come before fetch"

    def test_regression_comment_block_exists(self, dashboard_js: str):
        assert "REGRESSION 2026-05-25: Dashboard polled /admin/sqlery/undefined" in dashboard_js
