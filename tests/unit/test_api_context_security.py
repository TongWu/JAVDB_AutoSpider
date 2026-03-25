import importlib
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def _reload_context_module():
    module = importlib.import_module("apps.api.services.context")
    return importlib.reload(module)


def test_cookie_secure_defaults_to_true(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("COOKIE_ALLOW_INSECURE", raising=False)

    context_module = _reload_context_module()

    assert context_module.COOKIE_SECURE is True


def test_cookie_allow_insecure_can_disable_secure_flag(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.setenv("COOKIE_ALLOW_INSECURE", "true")

    context_module = _reload_context_module()

    assert context_module.COOKIE_SECURE is False


def test_cookie_secure_explicit_false_overrides_default(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.delenv("COOKIE_ALLOW_INSECURE", raising=False)

    context_module = _reload_context_module()

    assert context_module.COOKIE_SECURE is False
