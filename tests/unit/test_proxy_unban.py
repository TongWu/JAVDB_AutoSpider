"""Tests for the ``apps.cli.ops.proxy_unban`` ops CLI orchestration logic.

The coordinator HTTP path itself is already covered by
``test_proxy_coordinator_client.py``; these tests only cover this script's
own logic (which proxies get targeted, failure counting, exit codes) by
mocking ``ProxyCoordinatorClient``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from apps.cli.ops import proxy_unban


@pytest.fixture(autouse=True)
def _configured_coordinator(monkeypatch):
    monkeypatch.setattr(proxy_unban, "PROXY_COORDINATOR_URL", "https://coord.example.test")
    monkeypatch.setattr(proxy_unban, "PROXY_COORDINATOR_TOKEN", "dummy-token")
    monkeypatch.setattr(
        proxy_unban,
        "PROXY_POOL",
        [{"name": "Alpha"}, {"name": "Beta"}, {"name": ""}],
    )


def _run(argv):
    with patch.object(sys, "argv", ["proxy_unban.py", *argv]):
        return proxy_unban.main()


class TestProxyUnbanCli:
    def test_all_unbans_every_named_proxy_and_skips_blank_names(self):
        client = MagicMock()
        with patch.object(proxy_unban, "ProxyCoordinatorClient", return_value=client):
            rc = _run(["--all"])
        assert rc == 0
        assert client.report.call_args_list == [
            (("Alpha",), {"kind": "unban", "reason": "ops manual clear"}),
            (("Beta",), {"kind": "unban", "reason": "ops manual clear"}),
        ]
        client.close.assert_called_once()

    def test_explicit_proxy_list_targets_only_those_names(self):
        client = MagicMock()
        with patch.object(proxy_unban, "ProxyCoordinatorClient", return_value=client):
            rc = _run(["--proxy", "Jeddah-ARM1", "--proxy", "Jeddah-ARM2"])
        assert rc == 0
        names = [c.args[0] for c in client.report.call_args_list]
        assert names == ["Jeddah-ARM1", "Jeddah-ARM2"]

    def test_custom_reason_is_forwarded(self):
        client = MagicMock()
        with patch.object(proxy_unban, "ProxyCoordinatorClient", return_value=client):
            _run(["--proxy", "Alpha", "--reason", "BFR-024 residual ban cleanup"])
        assert client.report.call_args.kwargs["reason"] == "BFR-024 residual ban cleanup"

    def test_partial_failure_still_attempts_every_proxy_and_exits_nonzero(self):
        client = MagicMock()
        client.report.side_effect = [
            None,
            proxy_unban.CoordinatorUnavailable("boom"),
        ]
        with patch.object(proxy_unban, "ProxyCoordinatorClient", return_value=client):
            rc = _run(["--all"])
        assert rc == 1
        assert client.report.call_count == 2
        client.close.assert_called_once()

    def test_neither_all_nor_proxy_is_a_usage_error(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_missing_coordinator_config_short_circuits(self, monkeypatch):
        monkeypatch.setattr(proxy_unban, "PROXY_COORDINATOR_URL", "")
        client_cls = MagicMock()
        with patch.object(proxy_unban, "ProxyCoordinatorClient", client_cls):
            rc = _run(["--all"])
        assert rc == 1
        client_cls.assert_not_called()
