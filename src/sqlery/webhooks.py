# #CLEANUP 2026-05-14: This module has been moved to sqlery.django_sqlery.webhooks
# (CLEAN-04, Phase 04). This stub re-exports for backward compatibility.
# Remove after 2027-05-14.
#
# Module-identity aliasing: we replace this module's entry in sys.modules with
# the canonical module so that `from sqlery import webhooks; webhooks.httpx`
# and `patch.object(webhooks, "_post_pinned", ...)` operate on the SAME binding as
# the canonical module — required by tests/unit/test_webhooks.py.
# (The HTTP client was `requests` before 0.25.0; webhooks now use `httpx`.)
import sys as _sys

from sqlery.django_sqlery import webhooks as _canonical

_sys.modules[__name__] = _canonical
