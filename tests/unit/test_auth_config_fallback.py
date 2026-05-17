"""Verifies that apps.api.infra.auth respects the env > config.py > default
precedence on its sensitive settings (single config source enabler for
self-hosters who maintain config.py instead of a separate .env.api file)."""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_auth_module():
    """Force-reimport so module-level constants are re-evaluated against
    the current env + config.py state."""
    if "apps.api.infra.auth" in sys.modules:
        del sys.modules["apps.api.infra.auth"]
    return importlib.import_module("apps.api.infra.auth")


def test_env_wins_over_config(monkeypatch):
    """Env var beats config.py for ADMIN_USERNAME."""
    import javdb.infra.config as _cfg
    monkeypatch.setattr(
        _cfg,
        "_config_module",
        type("FakeCfg", (), {"ADMIN_USERNAME": "from-config-py"}),
    )
    monkeypatch.setenv("ADMIN_USERNAME", "from-env")
    # API_SECRET_KEY must be set to >= 32 chars so the module imports cleanly
    monkeypatch.setenv("API_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("ADMIN_PASSWORD", "any")
    auth = _reload_auth_module()
    assert auth.ADMIN_USERNAME == "from-env"


def test_config_used_when_env_unset(monkeypatch):
    """config.py provides ADMIN_USERNAME when env is unset."""
    import javdb.infra.config as _cfg
    monkeypatch.setattr(
        _cfg,
        "_config_module",
        type(
            "FakeCfg",
            (),
            {
                "ADMIN_USERNAME": "from-config-py",
                "API_SECRET_KEY": "y" * 40,
                "ADMIN_PASSWORD": "secret-from-config",
            },
        ),
    )
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("API_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    auth = _reload_auth_module()
    assert auth.ADMIN_USERNAME == "from-config-py"


def test_default_used_when_both_unset(monkeypatch):
    """The hardcoded default 'admin' applies when env AND config.py both lack the field."""
    import javdb.infra.config as _cfg
    monkeypatch.setattr(_cfg, "_config_module", type("FakeCfg", (), {}))
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.setenv("API_SECRET_KEY", "z" * 40)
    monkeypatch.setenv("ADMIN_PASSWORD", "any")
    auth = _reload_auth_module()
    assert auth.ADMIN_USERNAME == "admin"


def test_empty_env_falls_through_to_config(monkeypatch):
    """Empty env var ('') is treated as 'unset' and falls through to config.py."""
    import javdb.infra.config as _cfg
    monkeypatch.setattr(
        _cfg,
        "_config_module",
        type("FakeCfg", (), {"ADMIN_USERNAME": "from-config-py"}),
    )
    monkeypatch.setenv("ADMIN_USERNAME", "")  # empty, not deleted
    monkeypatch.setenv("API_SECRET_KEY", "a" * 40)
    monkeypatch.setenv("ADMIN_PASSWORD", "any")
    auth = _reload_auth_module()
    assert auth.ADMIN_USERNAME == "from-config-py"


def test_context_does_not_auto_load_dotenv():
    """Regression: apps/api/services/context.py must not auto-load .env files.

    A previous version called ``load_dotenv(REPO_ROOT / ".env")`` at module
    import time. That had two surprising side-effects:

    1. Values in the repo-root .env file silently entered ``os.environ`` —
       overriding config.py via the env>config precedence in _resolve().
    2. Stale .env entries (from earlier debugging sessions) confused
       self-hosters editing config.py to set, e.g., ADMIN_PASSWORD: their
       new value would be ignored in favor of the leaked .env value.

    .env is now strictly for tools that read it directly (docker-compose
    env_file:, the cron-bash entrypoint). The Python API process never
    reads it.
    """
    import inspect
    import apps.api.services.context as ctx
    source = inspect.getsource(ctx)
    assert "load_dotenv" not in source, (
        "context.py is calling load_dotenv() — that auto-leaks .env values "
        "into os.environ at import time and silently overrides config.py."
    )
    assert "from dotenv" not in source, "Drop the dotenv import too."
