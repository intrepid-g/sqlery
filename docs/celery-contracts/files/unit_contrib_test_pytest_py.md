### `t/unit/contrib/test_pytest.py`

This file has one test, and it covers test-tooling wiring only (the Celery pytest plugin). It has no queue or worker guarantees. The portable idea is that sqlery's test helpers must not add noise or break the host test runner.

**Test-helper registration**
- Register sqlery's test plugin, then run a test marked with sqlery's custom marker plus arguments (for example, a marker carrying a config option). Expect: the test passes and the runner emits no "unknown marker" warning.
