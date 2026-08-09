"""Canonical runtime-config accessor seam (issue #228).

Core code reads merged runtime config through ``javdb.infra.runtime_config``
rather than importing the application layer. The accessor must fail closed when
no provider is registered, and otherwise return exactly what the registered
provider returns.
"""

import pytest

from javdb.infra import runtime_config


def test_get_runtime_config_uses_registered_provider(monkeypatch):
    monkeypatch.setattr(runtime_config, "_provider", None)
    runtime_config.register_provider(lambda: {"PROXY_POOL": [{"http": "x"}]})
    assert runtime_config.get_runtime_config() == {"PROXY_POOL": [{"http": "x"}]}


def test_get_runtime_config_fails_closed_when_no_provider(monkeypatch):
    # No provider registered -> must raise, never return an empty/de-proxied
    # config that would let a proxy-dependent caller scrape direct (issue #226).
    monkeypatch.setattr(runtime_config, "_provider", None)
    with pytest.raises(RuntimeError):
        runtime_config.get_runtime_config()


def test_provider_errors_propagate(monkeypatch):
    def boom():
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(runtime_config, "_provider", boom)
    with pytest.raises(RuntimeError):
        runtime_config.get_runtime_config()


def test_register_provider_last_wins(monkeypatch):
    monkeypatch.setattr(runtime_config, "_provider", None)
    runtime_config.register_provider(lambda: {"v": 1})
    runtime_config.register_provider(lambda: {"v": 2})
    assert runtime_config.get_runtime_config() == {"v": 2}


def test_config_service_registers_provider_on_import(monkeypatch):
    # Importing the API config service must wire its loader behind the accessor,
    # so the indexer never needs to import apps.* itself. Reset the provider and
    # reload the module so the assertion exercises the import-time registration
    # itself, not residual global state left by an earlier test or import.
    import importlib

    from apps.api.services import config_service

    monkeypatch.setattr(runtime_config, "_provider", None)
    importlib.reload(config_service)

    assert runtime_config._provider is config_service.load_runtime_config
