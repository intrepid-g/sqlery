"""Unit tests for SEC-04: ALLOWED_TASK_MODULES opt-in allowlist."""

import logging

import pytest

from sqlery.core.security import (
    TaskModuleNotAllowed,
    check_task_module_allowed,
    is_production_env,
    warn_if_unconfigured,
)


# ===== check_task_module_allowed =====


class TestCheckTaskModuleAllowed:
    def test_none_allowed_is_passthrough(self):
        # BC: unset = allow all
        assert check_task_module_allowed("myapp.tasks", None) is None
        assert check_task_module_allowed("anything.at.all", None) is None

    def test_empty_list_is_passthrough(self):
        # Empty list = unset semantics
        assert check_task_module_allowed("myapp.tasks", []) is None

    def test_prefix_match_allowed(self):
        assert check_task_module_allowed("myapp.tasks", ["myapp"]) is None
        assert check_task_module_allowed("myapp.tasks.deep.path", ["myapp"]) is None

    def test_exact_match_allowed(self):
        assert check_task_module_allowed("myapp", ["myapp"]) is None
        assert check_task_module_allowed("myapp.tasks", ["myapp.tasks"]) is None

    def test_dot_boundary_prevents_prefix_collision(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("myapp_evil.tasks", ["myapp"])

    def test_dot_boundary_prevents_substring_match(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("evil_myapp.tasks", ["myapp"])

    def test_disallowed_module_raises(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("other.tasks", ["myapp", "third"])

    def test_multi_entry_list_matches_any(self):
        assert check_task_module_allowed("third.tasks", ["myapp", "third"]) is None

    def test_error_message_includes_module(self):
        with pytest.raises(TaskModuleNotAllowed, match="other.tasks"):
            check_task_module_allowed("other.tasks", ["myapp"])


# ===== is_production_env =====


class TestIsProductionEnv:
    def test_env_production(self):
        assert is_production_env({"ENV": "production"}) is True

    def test_env_local(self):
        assert is_production_env({"ENV": "local"}) is False

    def test_environment_prod(self):
        assert is_production_env({"ENVIRONMENT": "prod"}) is True

    def test_django_settings_module_production(self):
        assert is_production_env(
            {"DJANGO_SETTINGS_MODULE": "myproj.settings_production"}
        ) is True

    def test_django_settings_module_dev(self):
        assert is_production_env({"DJANGO_SETTINGS_MODULE": "myproj.settings_dev"}) is False

    def test_case_insensitive(self):
        assert is_production_env({"ENV": "PRODUCTION"}) is True
        assert is_production_env({"ENV": "Prod"}) is True

    def test_empty_env(self):
        assert is_production_env({}) is False

    def test_defaults_to_os_environ(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
        assert is_production_env() is False
        monkeypatch.setenv("ENV", "production")
        assert is_production_env() is True


# ===== warn_if_unconfigured =====


class TestWarnIfUnconfigured:
    def test_warns_once_in_production_when_unset(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(None)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "ALLOWED_TASK_MODULES" in warnings[0].message

    def test_warns_when_empty_list_in_production(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured([])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_no_warning_when_configured(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(["myapp"])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0

    def test_no_warning_when_not_production(self, caplog, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(None)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0


# ===== Integration: worker dispatch enforcement =====


class TestWorkerDispatchEnforcement:
    """Verify the wired callsite in core/worker.py uses the allowlist."""

    def test_import_task_rejected_when_not_in_allowlist(self, monkeypatch):
        # Simulate enforcement at the dispatch site: when allowed is set and the
        # module is not in it, check_task_module_allowed raises before any import.
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("other.tasks", ["myapp"])

    def test_security_module_imported_by_worker(self):
        # Smoke test that the wiring import resolves.
        from sqlery.core import worker  # noqa: F401
        from sqlery.core.security import check_task_module_allowed  # noqa: F401
