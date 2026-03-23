"""Tests for named login proxy resolution.

Login-queue routing helpers (use_login_queue_priority, should_delegate_login_task)
are covered by tests/test_spider_integration.py::TestLoginQueueHelpers.
"""

from unittest.mock import patch

import scripts.spider.session as session_mod


class TestResolveLoginProxyEndpoints:
    def test_returns_none_when_unset(self):
        with patch.object(session_mod, 'LOGIN_PROXY_NAME', None):
            with patch.object(session_mod, 'PROXY_POOL', []):
                assert session_mod.resolve_login_proxy_endpoints() == (None, None)

    def test_resolves_matching_pool_entry(self):
        pool = [
            {'name': 'eu', 'http': 'http://eu:1', 'https': 'http://eu:1'},
            {'name': 'us', 'http': 'http://us:1'},
        ]
        with patch.object(session_mod, 'LOGIN_PROXY_NAME', 'eu'):
            with patch.object(session_mod, 'PROXY_POOL', pool):
                proxies, name = session_mod.resolve_login_proxy_endpoints()
        assert name == 'eu'
        assert proxies == {'http': 'http://eu:1', 'https': 'http://eu:1'}

    def test_missing_name_returns_none(self):
        with patch.object(session_mod, 'LOGIN_PROXY_NAME', 'missing'):
            with patch.object(session_mod, 'PROXY_POOL', [{'name': 'eu', 'http': 'http://x'}]):
                assert session_mod.resolve_login_proxy_endpoints() == (None, None)
