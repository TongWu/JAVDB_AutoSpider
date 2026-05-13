"""Unit tests for :mod:`packages.python.javdb_platform.config_helper`."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform import config_helper  # noqa: E402


def test_env_or_cfg_str_absent_env_falls_back_to_cfg(monkeypatch):
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    with patch.object(config_helper, "cfg", return_value="https://from-config.test"):
        assert config_helper.env_or_cfg_str("PROXY_COORDINATOR_URL") == (
            "https://from-config.test"
        )


def test_env_or_cfg_str_empty_env_disables_no_cfg_fallback(monkeypatch):
    """Explicit empty PROXY_COORDINATOR_* must not rehydrate from config.py."""
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "")
    with patch.object(
        config_helper,
        "cfg",
        return_value="https://must-not-be-used.test",
    ):
        assert config_helper.env_or_cfg_str("PROXY_COORDINATOR_URL") == ""


def test_env_or_cfg_str_whitespace_only_env_disables_no_cfg_fallback(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "  \t  ")
    with patch.object(
        config_helper,
        "cfg",
        return_value="https://must-not-be-used.test",
    ):
        assert config_helper.env_or_cfg_str("PROXY_COORDINATOR_URL") == ""


def test_env_or_cfg_str_nonempty_env_wins_over_cfg(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://from-env.test")
    with patch.object(
        config_helper,
        "cfg",
        return_value="https://from-config.test",
    ):
        assert config_helper.env_or_cfg_str("PROXY_COORDINATOR_URL") == (
            "https://from-env.test"
        )
