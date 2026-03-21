"""Tests for parallel login-queue routing and named login proxy resolution."""

from unittest.mock import patch

import scripts.spider.session as session_mod
from scripts.spider.parallel_login import (
    should_delegate_login_task,
    use_login_queue_priority,
)


class TestParallelLoginHelpers:
    def test_use_login_queue_priority_logged_in_worker(self):
        assert use_login_queue_priority(None, 'A', 2, 2) is True
        assert use_login_queue_priority(None, 'A', 2, 0) is False

    def test_use_login_queue_priority_named_proxy(self):
        assert use_login_queue_priority('LoginP', 'LoginP', None, 1) is True
        assert use_login_queue_priority('LoginP', 'Other', None, 1) is False

    def test_should_delegate(self):
        assert should_delegate_login_task('LoginP', 'Other') is True
        assert should_delegate_login_task('LoginP', 'LoginP') is False
        assert should_delegate_login_task(None, 'Any') is False


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
