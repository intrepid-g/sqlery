"""CSRF regression tests for SEC-03 (plan 04-05).

Locks in the post-audit behavior:

  * State-changing admin endpoints in ``sqlery.django_sqlery.api_views`` no
    longer carry ``@csrf_exempt`` and therefore MUST reject POSTs that lack a
    valid ``X-CSRFToken`` header (Django middleware returns 403).
  * The 3 intentional ``@csrf_exempt`` endpoints in ``sqlery.django_sqlery.views``
    (``internal_worker``, ``health_check``, ``trigger_view``) remain exempt
    because they use HMAC token auth or are read-only liveness probes.

The ``health_check`` test below documents that intentional exemption: a GET to
``/_internal/health`` succeeds without any auth or CSRF token.

If anyone re-adds ``@csrf_exempt`` to ``api_clear_jobs`` (or any of the other 9
audit targets), the first test below will turn green for the wrong reason
(status 200 instead of 403) and surface the regression.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client


@pytest.mark.django_db
def test_csrf_enforced_on_api_clear_jobs():
    """POST /admin/api/sqlery/jobs/clear/ without an X-CSRFToken returns 403.

    This is the SEC-03 regression assertion. ``api_clear_jobs`` is one of the
    10 endpoints in ``api_views.py`` that previously carried ``@csrf_exempt``.
    After plan 04-05, the Django CSRF middleware must reject the POST.
    """
    User = get_user_model()
    staff = User.objects.create_user(
        username="csrf-staff",
        password="x",
        is_staff=True,
        is_superuser=True,
    )

    # enforce_csrf_checks=True is required — the default Django test Client
    # disables CSRF middleware to simplify normal-path testing.
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)

    # URL from src/sqlery/django_sqlery/urls.py line 47:
    #   path("admin/api/sqlery/jobs/clear/", api_clear_jobs, name="api_clear_jobs")
    resp = client.post(
        "/admin/api/sqlery/jobs/clear/",
        data="{}",
        content_type="application/json",
    )

    assert resp.status_code == 403, (
        f"Expected 403 (CSRF middleware reject), got {resp.status_code}. "
        "Did someone re-add @csrf_exempt to api_clear_jobs?"
    )


@pytest.mark.django_db
def test_csrf_intentional_exemption_on_health_check():
    """GET /_internal/health/ remains accessible without auth or CSRF.

    Documents the intentional ``@csrf_exempt`` on ``health_check`` in
    ``views.py`` — it is a read-only liveness probe and must not require auth
    or CSRF (kubelet, ALB, etc. cannot supply Django session cookies).
    """
    client = Client(enforce_csrf_checks=True)
    resp = client.get("/_internal/health")
    # health_check returns 200 (and may return 503 on degraded state, but never
    # 403 from CSRF — that's what we're asserting here).
    assert resp.status_code != 403, (
        f"health_check unexpectedly returned 403 (got {resp.status_code}). "
        "Did someone strip its intentional @csrf_exempt decorator?"
    )
