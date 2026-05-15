"""Regression test for CLEAN-04: both webhook import paths must resolve.

Phase 04 Plan 01 moved sqlery.webhooks → sqlery.django_sqlery.webhooks (canonical)
and left a dated BC stub at the old path. Both import paths must yield the same
callable.
"""


def test_canonical_import_path_succeeds():
    """The canonical location must export send_webhook_with_retry."""
    from sqlery.django_sqlery.webhooks import send_webhook_with_retry

    assert callable(send_webhook_with_retry)


def test_bc_stub_import_path_succeeds():
    """The old import path must still work via the BC stub."""
    from sqlery.webhooks import send_webhook_with_retry

    assert callable(send_webhook_with_retry)


def test_both_paths_resolve_to_same_callable():
    """Both import paths must reference the identical function object."""
    from sqlery.webhooks import send_webhook_with_retry as bc_path
    from sqlery.django_sqlery.webhooks import send_webhook_with_retry as canonical

    assert bc_path is canonical
