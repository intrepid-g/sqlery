"""Quarantined review findings from PR 26 (c-review-with-demo).

Each test proves one already-filed issue. All tests are committed
``@pytest.mark.skip``'d so the suite stays green; unskip to re-verify.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "sqlery"


# ---------------------------------------------------------------------------
# 1. https://github.com/intrepid-g/sqlery/issues/28
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/28 — "
        "{{ str(worker.id)[:8] }} 500s /workers because str is not a Jinja2 global"
    )
)
def test_workers_page_renders_with_a_worker_row(tmp_path, monkeypatch):
    """Standalone /workers 500s once a worker row exists (workers.html:26)."""
    pytest.importorskip("uvicorn")

    db_path = tmp_path / "workers_test.sqlite3"
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "disabled")
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")

    import sqlery.compat as compat_mod

    compat_mod._backend = None
    compat_mod._config = None

    from sqlery.compat import initialize, get_backend

    initialize(database_url=f"sqlite:///{db_path}", enable_daemon=False)
    backend = get_backend()

    from fastapi.testclient import TestClient
    from sqlery.fastapi_sqlery import app as app_module

    client = TestClient(app_module.app, raise_server_exceptions=False)

    # Negative control: zero worker rows must return 200 even today. This is
    # exactly why the {{ str(...) }} bug in the per-row loop body was never
    # caught — the loop body never runs against an empty table.
    zero_row_resp = client.get("/workers")
    assert zero_row_resp.status_code == 200, (
        "zero-row /workers should already return 200; if not, the harness "
        f"(not the app) is broken. Got {zero_row_resp.status_code}"
    )

    # get_worker_heartbeats(active_only=False) in the /workers route applies
    # no status/heartbeat filter, so any inserted row satisfies the query.
    from sqlery.core.models import Worker

    with backend._get_session() as session:
        worker = Worker(node_id="test-node", pid=12345, status="idle", queues=["default"])
        session.add(worker)
        session.commit()

    resp = client.get("/workers")
    assert resp.status_code == 200, (
        f"expected 200 with one worker row, got {resp.status_code}: {resp.text[:500]}"
    )


# ---------------------------------------------------------------------------
# 2. https://github.com/intrepid-g/sqlery/issues/35
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/35 — "
        "duplicate admin templates under src/sqlery/templates have diverged from "
        "the live django_sqlery copies"
    )
)
@pytest.mark.django_db
def test_admin_templates_have_a_single_live_copy():
    """Django loads only django_sqlery's templates; the src/sqlery copies are dead
    but have already diverged, so anyone editing the dead copy silently loses work.
    """
    from django.template.loader import get_template

    names = [
        "admin/sqlery/dashboard.html",
        "admin/sqlery/change_list.html",
        "admin/sqlery/scheduledtask/change_form.html",
    ]
    django_sqlery_templates = SRC / "django_sqlery" / "templates"
    for name in names:
        template = get_template(name)
        origin = Path(template.origin.name).resolve()
        assert str(origin).startswith(str(django_sqlery_templates)), (
            f"{name} resolved outside django_sqlery/templates/: {origin}"
        )

    stale_dashboard = SRC / "templates" / "admin" / "sqlery" / "dashboard.html"
    live_dashboard = django_sqlery_templates / "admin" / "sqlery" / "dashboard.html"
    assert not stale_dashboard.exists() or stale_dashboard.read_bytes() == live_dashboard.read_bytes(), (
        f"{stale_dashboard} is a dead copy that has diverged from {live_dashboard}"
    )


# ---------------------------------------------------------------------------
# 3. https://github.com/intrepid-g/sqlery/issues/36
# ---------------------------------------------------------------------------


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of a 3- or 6-digit hex colour (matches
    tests/test_dashboard_dark_theme.py's helper of the same name)."""
    digits = hex_colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    l_a = _relative_luminance(hex_a)
    l_b = _relative_luminance(hex_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


DARK_ADMIN_BODY_BG = "#121212"


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/36 — "
        ".job-link/.task-link hardcode #417690, 3.79:1 against dark admin body-bg, "
        "below WCAG AA 4.5:1"
    )
)
@pytest.mark.parametrize(
    "template_path,selector",
    [
        (
            SRC / "django_sqlery" / "templates" / "admin" / "sqlery" / "dashboard.html",
            ".job-link",
        ),
        (
            SRC / "django_sqlery" / "templates" / "admin" / "sqlery" / "change_list.html",
            ".task-link",
        ),
    ],
    ids=["dashboard.job-link", "change_list.task-link"],
)
def test_dashboard_link_colour_meets_contrast_in_dark_theme(template_path, selector):
    css = template_path.read_text()
    escaped = re.escape(selector)
    match = re.search(
        rf"{escaped}\s*\{{[^{{}}]*?color:\s*(#[0-9a-fA-F]{{3,6}})", css
    )
    assert match, f"{selector} rule with a color declaration not found in {template_path}"
    colour = match.group(1)
    ratio = _contrast_ratio(colour, DARK_ADMIN_BODY_BG)
    assert ratio >= 4.5, (
        f"{selector} colour {colour} against dark admin body-bg {DARK_ADMIN_BODY_BG} "
        f"is {ratio:.2f}:1, below WCAG AA 4.5:1"
    )


# ---------------------------------------------------------------------------
# 4. https://github.com/intrepid-g/sqlery/issues/37
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/37 — "
        "_unpaired_light_utilities checks 'dark:' over the whole class attribute, "
        "so a Jinja branch missing its own dark: variant is not caught"
    )
)
def test_dark_variant_check_validates_each_jinja_branch():
    from tests.test_dashboard_dark_theme import _unpaired_light_utilities

    markup = '<div class="{% if x %}bg-white{% else %}dark:bg-gray-900{% endif %}">'
    offenders = _unpaired_light_utilities(markup)
    assert len(offenders) == 1, (
        f"expected the light-only 'bg-white' branch (no own dark: variant) to be "
        f"flagged as 1 offender, got {offenders!r}"
    )
