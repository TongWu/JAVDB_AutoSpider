"""Tests for named login proxy resolution and spider_uses_proxy behaviour.

Login-queue routing helpers (use_login_queue_priority, should_delegate_login_task)
are covered by tests/integration/test_spider_integration.py::TestLoginQueueHelpers.
"""

from unittest.mock import patch, MagicMock, ANY

import scripts.spider.fetch.session as session_mod
import scripts.spider.fetch.fallback as fallback_mod
import scripts.spider.runtime.state as state_mod
from packages.python.javdb_platform.proxy_policy import (
    is_proxy_mode_disabled,
    normalize_proxy_mode,
    should_proxy_module,
)


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


class TestSpiderUsesProxyFlag:
    """Verify that spider_uses_proxy=False skips implicit proxy resolution."""

    def _patch_prerequisites(self):
        """Return patches that allow attempt_login_refresh to proceed past
        the initial feature/credential gates."""
        return [
            patch.object(session_mod, 'LOGIN_FEATURE_AVAILABLE', True),
            patch.object(session_mod, 'JAVDB_USERNAME', 'user'),
            patch.object(session_mod, 'JAVDB_PASSWORD', 'pass'),
            patch.object(state_mod, 'login_total_budget', 0),
            patch.object(state_mod, 'login_total_attempts', 0),
            patch.object(state_mod, 'login_attempted', False),
        ]

    def test_no_proxy_skips_pool_and_named_proxy(self):
        """spider_uses_proxy=False must not resolve proxies from
        LOGIN_PROXY_NAME or global_proxy_pool."""
        pool_proxy = MagicMock()
        pool_proxy.get_current_proxy.return_value = {'http': 'http://pool:1'}
        pool_proxy.get_current_proxy_name.return_value = 'PoolProxy'

        named_pool = [{'name': 'named', 'http': 'http://named:1'}]

        patches = self._patch_prerequisites() + [
            patch.object(session_mod, 'LOGIN_PROXY_NAME', 'named'),
            patch.object(session_mod, 'PROXY_POOL', named_pool),
            patch.object(state_mod, 'global_proxy_pool', pool_proxy),
        ]

        mock_login = MagicMock(return_value=(True, 'cookie123', 'ok'))

        for p in patches:
            p.start()
        try:
            # login.py calls sys.exit(1) at import time when config.py is missing (typical CI);
            # mock.patch loads that module to patch login_with_retry, so sys.exit must be stubbed first.
            with patch('sys.exit'), patch(
                'packages.python.javdb_integrations.login.login_with_retry',
                mock_login,
            ), patch(
                'packages.python.javdb_integrations.login.update_config_file',
                return_value=False,
            ):
                success, _cookie, _proxy_name = session_mod.attempt_login_refresh(
                    spider_uses_proxy=False,
                )
            assert success is True
            mock_login.assert_called_once()
            _, kwargs = mock_login.call_args
            assert kwargs.get('proxies') is None, \
                "login_with_retry must receive proxies=None when spider_uses_proxy=False"
        finally:
            for p in reversed(patches):
                p.stop()

    def test_uses_proxy_true_resolves_pool(self):
        """spider_uses_proxy=True (default) should still resolve from pool
        when no explicit_proxies given."""
        pool_proxy = MagicMock()
        pool_proxy.get_current_proxy.return_value = {
            'http': 'http://pool:1',
            'https': 'http://pool:1',
        }
        pool_proxy.get_current_proxy_name.return_value = 'PoolProxy'

        patches = self._patch_prerequisites() + [
            patch.object(session_mod, 'LOGIN_PROXY_NAME', None),
            patch.object(session_mod, 'PROXY_POOL', []),
            patch.object(state_mod, 'global_proxy_pool', pool_proxy),
        ]

        mock_login = MagicMock(return_value=(True, 'cookie456', 'ok'))

        for p in patches:
            p.start()
        try:
            with patch('sys.exit'), patch(
                'packages.python.javdb_integrations.login.login_with_retry',
                mock_login,
            ), patch(
                'packages.python.javdb_integrations.login.update_config_file',
                return_value=False,
            ):
                success, _cookie, _proxy_name = session_mod.attempt_login_refresh(
                    spider_uses_proxy=True,
                )
            assert success is True
            _, kwargs = mock_login.call_args
            assert kwargs.get('proxies') is not None, \
                "login_with_retry should receive pool proxies when spider_uses_proxy=True"
            assert kwargs['proxies']['http'] == 'http://pool:1'
        finally:
            for p in reversed(patches):
                p.stop()


class TestLoginRefreshForSpider:
    """Tests for _login_refresh_for_spider helper in fallback.py."""

    def test_no_proxy_passes_spider_uses_proxy_false(self):
        """_login_refresh_for_spider(False) calls attempt_login_refresh
        with spider_uses_proxy=False."""
        with patch.object(
            fallback_mod, 'attempt_login_refresh',
            return_value=(False, None, None),
        ) as mock_alr:
            fallback_mod._login_refresh_for_spider(False)
            mock_alr.assert_called_once_with(spider_uses_proxy=False)

    def test_with_proxy_snapshots_pool(self):
        """_login_refresh_for_spider(True) snapshots the current pool proxy
        and passes explicit_proxies + spider_uses_proxy=True."""
        pool = MagicMock()
        pool.get_current_proxy.return_value = {
            'http': 'http://snap:1',
            'https': 'http://snap:2',
        }
        pool.get_current_proxy_name.return_value = 'SnapProxy'

        with patch.object(state_mod, 'global_proxy_pool', pool):
            with patch.object(
                fallback_mod, 'attempt_login_refresh',
                return_value=(True, 'c', 'SnapProxy'),
            ) as mock_alr:
                result = fallback_mod._login_refresh_for_spider(True)

            mock_alr.assert_called_once_with(
                explicit_proxies={'http': 'http://snap:1', 'https': 'http://snap:2'},
                explicit_proxy_name='SnapProxy',
                spider_uses_proxy=True,
            )
            assert result[0] is True

    def test_with_proxy_but_empty_pool(self):
        """_login_refresh_for_spider(True) with no pool falls back to bare
        spider_uses_proxy=True."""
        with patch.object(state_mod, 'global_proxy_pool', None):
            with patch.object(
                fallback_mod, 'attempt_login_refresh',
                return_value=(False, None, None),
            ) as mock_alr:
                fallback_mod._login_refresh_for_spider(True)
            mock_alr.assert_called_once_with(spider_uses_proxy=True)


class TestNoProxyFallbackShortCircuit:
    """Verify that no-proxy mode skips Phase 2 (proxy pool iteration)."""

    def test_index_no_proxy_skips_phase2(self):
        """fetch_index_page_with_fallback with use_proxy=False must not
        enter Phase 2 proxy iteration even when a pool is configured."""
        pool = MagicMock()
        pool.get_proxy_count.return_value = 3
        pool.mark_failure_and_switch.return_value = True

        with patch.object(state_mod, 'global_proxy_pool', pool), \
             patch.object(fallback_mod, 'can_attempt_login', return_value=False), \
             patch.object(state_mod, 'get_page', return_value=None):
            result = fallback_mod.fetch_index_page_with_fallback(
                page_url='http://javdb.com/1',
                session=MagicMock(),
                use_cookie=True,
                use_proxy=False,
                use_cf_bypass=False,
                page_num=1,
            )

        pool.mark_failure_and_switch.assert_not_called()
        assert result[1] is False  # has_movie_list

    def test_detail_no_proxy_skips_phase2(self):
        """fetch_detail_page_with_fallback with use_proxy=False must not
        enter Phase 2 proxy iteration."""
        pool = MagicMock()
        pool.get_proxy_count.return_value = 3
        pool.mark_failure_and_switch.return_value = True

        with patch.object(state_mod, 'global_proxy_pool', pool), \
             patch.object(fallback_mod, 'can_attempt_login', return_value=False), \
             patch.object(state_mod, 'get_page', return_value=None):
            result = fallback_mod.fetch_detail_page_with_fallback(
                detail_url='http://javdb.com/v/abc',
                session=MagicMock(),
                use_cookie=True,
                use_proxy=False,
                use_cf_bypass=False,
                entry_index='1/10',
            )

        pool.mark_failure_and_switch.assert_not_called()
        assert result[5] is False  # parse_success


class TestIndexCopyrightRestrictionLogin:
    """Verify that copyright restriction pages are treated as login-required."""

    def test_index_copyright_restriction_triggers_login_retry(self):
        copyright_html = (
            "<html><body>Due to copyright restrictions, "
            "this page is not available in your country.</body></html>"
        )
        valid_index_html = (
            "<html><body><div class='movie-list'>"
            "<div class='item'></div>"
            "</div></body></html>"
        )

        with patch.object(state_mod, 'global_proxy_pool', None), \
             patch.object(fallback_mod, 'can_attempt_login', return_value=True), \
             patch.object(fallback_mod, '_login_refresh_for_spider', return_value=(True, 'ck', None)) as login_refresh, \
             patch.object(fallback_mod, '_sleep_between_fetches'), \
             patch.object(state_mod, 'get_page', side_effect=[copyright_html, valid_index_html]):
            result = fallback_mod.fetch_index_page_with_fallback(
                page_url='http://javdb.com/actors/abc',
                session=MagicMock(),
                use_cookie=True,
                use_proxy=False,
                use_cf_bypass=False,
                page_num=1,
                is_adhoc_mode=True,
            )

        login_refresh.assert_called_once_with(False)
        assert result[0] == valid_index_html
        assert result[1] is True  # has_movie_list


class TestDetailLoginRetrySendsCookie:
    """run_service sets use_cookie=False for daily runs; post-login retry must still attach cookie."""

    def test_after_login_refresh_no_proxy_forces_cookie_on_retry(self):
        cookies_used = []

        def get_page_tracked(url, session=None, use_cookie=False, **kwargs):
            cookies_used.append(use_cookie)
            return '<html><body>stub</body></html>'

        parse_seq = iter(
            [
                ([], '', '', '', '', False),
                (['mag'], 'a', '', '', '', True),
            ]
        )

        def parse_side_effect(html, entry_index, **kwargs):
            return next(parse_seq)

        with patch.object(state_mod, 'global_proxy_pool', None), \
             patch.object(session_mod, 'LOGIN_FEATURE_AVAILABLE', True), \
             patch.object(state_mod, 'login_attempted', False), \
             patch.object(fallback_mod, '_login_refresh_for_spider', return_value=(True, 'ck', None)), \
             patch.object(fallback_mod, '_sleep_between_fetches'), \
             patch.object(fallback_mod, 'is_login_page', return_value=False), \
             patch.object(state_mod, 'get_page', side_effect=get_page_tracked), \
             patch.object(fallback_mod, 'parse_detail', side_effect=parse_side_effect):
            result = fallback_mod.fetch_detail_page_with_fallback(
                detail_url='http://javdb.com/v/abc',
                session=MagicMock(),
                use_cookie=False,
                use_proxy=False,
                use_cf_bypass=False,
                entry_index='1/10',
            )

        assert result[5] is True  # parse_success
        assert cookies_used == [False, True], (
            'initial fetch may omit cookie in daily mode; retry after login must send it'
        )


class TestProxyModeDisabled:
    """Tests for PROXY_MODE='None' / 'none' disabling proxy globally."""

    def test_is_proxy_mode_disabled_none_string(self):
        assert is_proxy_mode_disabled('None') is True
        assert is_proxy_mode_disabled('none') is True
        assert is_proxy_mode_disabled('NONE') is True
        assert is_proxy_mode_disabled(' None ') is True

    def test_is_proxy_mode_disabled_active_modes(self):
        assert is_proxy_mode_disabled('pool') is False
        assert is_proxy_mode_disabled('single') is False

    def test_normalize_proxy_mode(self):
        assert normalize_proxy_mode(None) == 'none'
        assert normalize_proxy_mode('') == 'none'
        assert normalize_proxy_mode('None') == 'none'
        assert normalize_proxy_mode('POOL') == 'pool'
        assert normalize_proxy_mode('single') == 'single'
        assert normalize_proxy_mode(' Pool ') == 'pool'

    def test_should_proxy_module_disabled_mode(self):
        """proxy_mode='none' makes should_proxy_module return False
        regardless of override or module list."""
        assert should_proxy_module('spider', True, ['all'], proxy_mode='none') is False
        assert should_proxy_module('spider', None, ['spider'], proxy_mode='none') is False

    def test_should_proxy_module_active_mode(self):
        """Normal proxy_mode values pass through to existing logic."""
        assert should_proxy_module('spider', None, ['spider'], proxy_mode='pool') is True
        assert should_proxy_module('spider', False, ['spider'], proxy_mode='pool') is False

    def test_setup_proxy_pool_skips_on_disabled(self):
        """setup_proxy_pool sets global_proxy_pool to None when mode is disabled."""
        orig_pool = state_mod.global_proxy_pool
        orig_mode = getattr(session_mod, 'PROXY_MODE', None)
        try:
            import scripts.spider.runtime.config as cfg_mod
            with patch.object(cfg_mod, 'PROXY_MODE', 'none'), \
                 patch('scripts.spider.runtime.state.PROXY_MODE', 'none'), \
                 patch('scripts.spider.runtime.state.PROXY_POOL', [{'name': 'X', 'http': 'http://x:1'}]):
                state_mod.setup_proxy_pool(True)
                assert state_mod.global_proxy_pool is None
        finally:
            state_mod.global_proxy_pool = orig_pool
            session_mod.PROXY_MODE = orig_mode
