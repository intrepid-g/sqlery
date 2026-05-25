"""Tests for the pure functions in sqlery.core.fork_safety."""

from sqlery.core.fork_safety import build_default_hooks, verify_no_open_connections


class TestBuildDefaultHooks:
    def test_no_backends(self):
        hooks = build_default_hooks(django_available=False, sqlalchemy_engine=None)
        assert hooks["pre_fork"] == []
        assert hooks["post_fork_parent"] == []
        assert hooks["post_fork_child"] == []

    def test_django_only(self):
        hooks = build_default_hooks(django_available=True, sqlalchemy_engine=None)
        assert hooks["pre_fork"] == ["django_close_all"]
        assert hooks["post_fork_parent"] == ["django_close_old"]
        assert hooks["post_fork_child"] == ["django_close_all"]

    def test_sqlalchemy_only(self):
        sentinel = object()
        hooks = build_default_hooks(django_available=False, sqlalchemy_engine=sentinel)
        assert hooks["pre_fork"] == ["sqlalchemy_dispose"]
        assert hooks["post_fork_parent"] == []
        assert hooks["post_fork_child"] == ["sqlalchemy_dispose"]

    def test_both_backends(self):
        sentinel = object()
        hooks = build_default_hooks(django_available=True, sqlalchemy_engine=sentinel)
        assert "django_close_all" in hooks["pre_fork"]
        assert "sqlalchemy_dispose" in hooks["pre_fork"]
        assert "django_close_old" in hooks["post_fork_parent"]
        assert "django_close_all" in hooks["post_fork_child"]
        assert "sqlalchemy_dispose" in hooks["post_fork_child"]

    def test_returns_all_three_phases(self):
        hooks = build_default_hooks(django_available=False)
        assert set(hooks.keys()) == {"pre_fork", "post_fork_parent", "post_fork_child"}


class TestVerifyNoOpenConnections:
    def test_clean_state(self):
        leaks = verify_no_open_connections(
            django_connection_names=None,
            sqlalchemy_pool_status=None,
        )
        assert leaks == []

    def test_django_leak(self):
        leaks = verify_no_open_connections(
            django_connection_names=["default"],
            sqlalchemy_pool_status=None,
        )
        assert len(leaks) == 1
        assert "default" in leaks[0]
        assert "django" in leaks[0]

    def test_multiple_django_leaks(self):
        leaks = verify_no_open_connections(
            django_connection_names=["default", "replica"],
        )
        assert len(leaks) == 2

    def test_sqlalchemy_leak(self):
        leaks = verify_no_open_connections(
            django_connection_names=None,
            sqlalchemy_pool_status={"checkedout": 3},
        )
        assert len(leaks) == 1
        assert "3" in leaks[0]
        assert "sqlalchemy" in leaks[0]

    def test_sqlalchemy_clean(self):
        leaks = verify_no_open_connections(
            django_connection_names=None,
            sqlalchemy_pool_status={"checkedout": 0},
        )
        assert leaks == []

    def test_both_leaking(self):
        leaks = verify_no_open_connections(
            django_connection_names=["default"],
            sqlalchemy_pool_status={"checkedout": 1},
        )
        assert len(leaks) == 2

    def test_empty_django_list_is_clean(self):
        leaks = verify_no_open_connections(
            django_connection_names=[],
        )
        assert leaks == []
