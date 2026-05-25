"""
Regression test for the dashboard.js 401/403 fix (REGRESSION 2026-05-25).

Since this is a frontend JavaScript bug in a Python project without JS test
infrastructure, we validate the fix by asserting that the exact conditional
paths exist in dashboard.js and are correctly ordered.
"""

from pathlib import Path

import pytest

DASHBOARD_JS = Path(__file__).parent.parent.parent / "src" / "sqlery" / "django_sqlery" / "static" / "sqlery" / "js" / "dashboard.js"


@pytest.fixture(scope="module")
def dashboard_js() -> str:
    assert DASHBOARD_JS.exists(), f"dashboard.js not found at {DASHBOARD_JS}"
    return DASHBOARD_JS.read_text()


class TestUpdateStatsFix:
    """Validate updateStats() response handling."""

    def test_429_branch_exists_before_401_403_check(self, dashboard_js: str):
        """The 429 early-return must come before the 401/403 guard."""
        idx_429 = dashboard_js.find('if (response.status === 429) return;')
        idx_401 = dashboard_js.find('if (response.status === 401 || response.status === 403)')
        assert idx_429 > 0, "429 check missing"
        assert idx_401 > 0, "401/403 check missing"
        assert idx_429 < idx_401, "429 must come before 401/403"

    def test_401_403_clears_interval_and_toasts(self, dashboard_js: str):
        """401/403 must clear the interval, update indicator, show toast, and return."""
        block = dashboard_js[
            dashboard_js.find('if (response.status === 401 || response.status === 403)'):
            dashboard_js.find('if (response.status === 401 || response.status === 403)') + 600
        ]
        assert 'clearInterval(autoRefreshInterval)' in block
        assert 'updateRefreshIndicator(false)' in block
        assert 'showToast("Session expired"' in block
        assert 'return;' in block

    def test_old_throw_is_commented_out(self, dashboard_js: str):
        """The original throw must be preserved as a comment."""
        assert '// if (!response.ok) throw new Error("Failed to fetch stats");' in dashboard_js

    def test_transient_non_ok_logs_warning_not_error(self, dashboard_js: str):
        """Non-401/403 non-OK must log console.warn and return, not throw."""
        assert 'console.warn(`Stats request failed (HTTP ${response.status})' in dashboard_js

    def test_no_uncommented_throw_after_401_403_guard(self, dashboard_js: str):
        """After the 401/403 guard, there must be no uncommented throw for non-OK."""
        start = dashboard_js.find('async function updateStats() {')
        end = dashboard_js.find('// Toggle stat card inline table', start)
        body = dashboard_js[start:end]
        guard_idx = body.find('if (response.status === 401 || response.status === 403)')
        after_guard = body[guard_idx:]
        # The old throw line is commented out; an uncommented throw must not exist after the guard.
        lines = after_guard.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('//') or stripped.startswith('*'):
                continue
            assert 'throw new Error("Failed to fetch stats")' not in stripped


class TestUpdateTasksFix:
    """Validate updateTasks() response handling."""

    def test_429_branch_exists(self, dashboard_js: str):
        """updateTasks must also handle 429."""
        assert 'if (response.status === 429) return;  // rate-limited — skip silently' in dashboard_js

    def test_401_403_guard_exists(self, dashboard_js: str):
        """updateTasks must have the same 401/403 guard."""
        assert 'if (response.status === 401 || response.status === 403) {' in dashboard_js

    def test_old_throw_commented_out(self, dashboard_js: str):
        assert '// if (!response.ok) throw new Error("Failed to fetch tasks");' in dashboard_js

    def test_transient_warns_not_throws(self, dashboard_js: str):
        assert 'console.warn(`Tasks request failed (HTTP ${response.status})' in dashboard_js


class TestPollFeedFix:
    """Validate pollFeed() response handling."""

    def test_401_403_guard_exists(self, dashboard_js: str):
        assert 'if (resp.status === 401 || resp.status === 403) {' in dashboard_js

    def test_401_403_clears_interval(self, dashboard_js: str):
        block = dashboard_js[
            dashboard_js.find('if (resp.status === 401 || resp.status === 403)'):
            dashboard_js.find('if (resp.status === 401 || resp.status === 403)') + 400
        ]
        assert 'clearInterval(autoRefreshInterval)' in block
        assert 'showToast("Session expired"' in block

    def test_429_silently_skips(self, dashboard_js: str):
        """pollFeed still silently skips non-OK (original behavior preserved)."""
        assert 'if (!resp.ok) return;' in dashboard_js


class TestRegressionsMd:
    """Validate the external record was updated."""

    def test_entry_exists(self):
        regressions = (
            Path(__file__).parent.parent.parent / "REGRESSIONS.md"
        ).read_text()
        assert "Dashboard \"Failed to fetch stats\" console error on session expiry" in regressions
        assert "Fix version:** v0.21.2" in regressions
