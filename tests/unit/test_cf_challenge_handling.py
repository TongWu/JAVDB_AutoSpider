"""Cloudflare challenge detection and site-wide-failure accounting.

Regression cover for BFR-024: javdb switched from the Turnstile
"Security Verification" page to a managed-challenge interstitial, which the
old ``'Security Verification' + 'turnstile'`` predicate could not see, and the
CF bypass services became unreachable at the same time. Between them the run
soft-banned all 28 proxies for a condition none of them caused.
"""

import requests
from unittest.mock import MagicMock, patch

from javdb.infra.request import RequestConfig, RequestHandler
from javdb.spider.html_validators import is_cf_challenge_page


# Trimmed from a real javdb.com 403 body captured 2026-08-07.
MANAGED_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
    '<meta name="robots" content="noindex,nofollow"></head><body>'
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=a272d5ea">'
    '</script></body></html>'
)

LEGACY_TURNSTILE_HTML = (
    '<html><head><title>Security Verification</title></head>'
    '<body><div class="cf-turnstile"></div></body></html>'
)


class TestIsCfChallengePage:
    def test_managed_challenge_detected(self):
        assert is_cf_challenge_page(MANAGED_CHALLENGE_HTML) is True

    def test_each_marker_suffices_on_its_own(self):
        """Either marker alone must trip it — they never co-occur on one page.

        The managed challenge carries "Just a moment...", the older Turnstile
        page carries "Security Verification"; requiring both would detect
        neither.
        """
        assert is_cf_challenge_page('<title>Just a moment...</title>') is True
        assert is_cf_challenge_page('<h1>Security Verification</h1>') is True

    def test_legacy_turnstile_still_detected(self):
        assert is_cf_challenge_page(LEGACY_TURNSTILE_HTML) is True

    def test_empty_html(self):
        assert is_cf_challenge_page('') is False
        assert is_cf_challenge_page(None) is False

    def test_real_page_not_flagged(self):
        html = '<html><body><div class="movie-list"><div class="item">x</div></div></body></html>'
        assert is_cf_challenge_page(html) is False

    def test_real_page_with_bot_management_beacon_not_flagged(self):
        """Regression: a zone with bot management enabled injects
        ``/cdn-cgi/challenge-platform/`` into every response — challenge or
        not. An earlier revision of ``_CF_CHALLENGE_MARKERS`` matched that
        script path directly, which false-positived on genuine successful
        fetches. Verified 2026-08-07 against a real CF-Bypass response from
        London-ARM1: full javdb.com content, movie-list and all, that still
        carried this beacon and was wrongly discarded as a challenge page.
        """
        html = (
            '<html><body><div class="movie-list"><div class="item">x</div></div>'
            '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
            '</body></html>'
        )
        assert is_cf_challenge_page(html) is False

    def test_cloudflare_block_page_is_not_a_challenge(self):
        """Error 1020 is IP-specific — it must keep counting against the proxy."""
        html = (
            '<html><head><title>Attention Required! | Cloudflare</title></head>'
            '<body>Sorry, you have been blocked</body></html>'
        )
        assert is_cf_challenge_page(html) is False


class TestChallengeIsNotChargedToProxy:
    def _handler(self):
        return RequestHandler(config=RequestConfig(use_curl_cffi=False))

    @patch('requests.Session.get')
    def test_403_challenge_skips_health_failure_record(self, mock_get):
        response = MagicMock()
        response.status_code = 403
        response.text = MANAGED_CHALLENGE_HTML
        mock_get.return_value = response

        handler = self._handler()
        handler._record_request_complete = MagicMock()

        body, error = handler._do_request(
            'https://javdb.com/', {}, None, timeout=30,
            context_msg='t', proxy_name='P1', report_health=True,
        )

        assert body == MANAGED_CHALLENGE_HTML
        assert isinstance(error, requests.HTTPError)
        handler._record_request_complete.assert_not_called()

    @patch('requests.Session.get')
    def test_403_non_challenge_still_records_failure(self, mock_get):
        response = MagicMock()
        response.status_code = 403
        response.text = '<html><body>plain forbidden</body></html>'
        mock_get.return_value = response

        handler = self._handler()
        handler._record_request_complete = MagicMock()

        handler._do_request(
            'https://javdb.com/', {}, None, timeout=30,
            context_msg='t', proxy_name='P1', report_health=True,
        )

        handler._record_request_complete.assert_called_once()

    def test_curl_cffi_403_challenge_skips_health_failure_record(self):
        """_fetch_direct prefers curl_cffi, so that branch needs the same guard."""
        response = MagicMock()
        response.status_code = 403
        response.text = MANAGED_CHALLENGE_HTML

        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        handler.use_curl_cffi = True
        handler.curl_cffi_session = MagicMock()
        handler.curl_cffi_session.get = MagicMock(return_value=response)
        handler._record_request_complete = MagicMock()

        body, error = handler._do_request_curl_cffi(
            'https://javdb.com/', {}, None, timeout=30,
            context_msg='t', proxy_name='P1', report_health=True,
        )

        assert body == MANAGED_CHALLENGE_HTML
        assert error is not None
        handler._record_request_complete.assert_not_called()

    def test_challenge_does_not_mark_proxy_failure_in_direct_mode(self):
        """mark_failure_and_switch() feeds PROXY_POOL_MAX_FAILURES — not for a challenge."""
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        handler._record_cf_event = MagicMock()
        handler._pause_between_attempts = MagicMock()
        handler._fetch_direct = MagicMock(
            return_value=(MANAGED_CHALLENGE_HTML, False, True),
        )
        pool = MagicMock()
        handler.proxy_pool = pool

        result = handler._get_page_direct(
            url='https://javdb.com/?page=1', session=handler.session,
            use_cookie=False, module_name='spider', max_retries=3,
            proxies={'http': 'http://10.0.0.5:7890'},
            use_proxy_pool_mode=True, proxy_name='P1',
        )

        assert result is None
        assert handler.last_site_challenge is True
        pool.mark_failure_and_switch.assert_not_called()

    def test_non_challenge_failure_still_switches_proxy(self):
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        handler._pause_between_attempts = MagicMock()
        handler._fetch_direct = MagicMock(return_value=(None, False, False))
        pool = MagicMock()
        pool.mark_failure_and_switch = MagicMock(return_value=False)
        handler.proxy_pool = pool

        handler._get_page_direct(
            url='https://javdb.com/?page=1', session=handler.session,
            use_cookie=False, module_name='spider', max_retries=3,
            proxies={'http': 'http://10.0.0.5:7890'},
            use_proxy_pool_mode=True, proxy_name='P1',
        )

        pool.mark_failure_and_switch.assert_called()

    def test_exhausted_fallback_on_challenge_sets_flag_and_skips_ban(self):
        """All fallbacks exhausted + challenge page => flag set, no ban raised.

        ``cf_bypass_ban_threshold=1`` would raise ProxyBannedError on the very
        first exhaustion if the challenge were charged to the proxy.
        """
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False, fallback_cooldown=0),
        )
        handler.config.cf_bypass_ban_threshold = 1
        handler.refresh_bypass_cache = MagicMock(return_value=False)
        handler._fetch_with_cf_bypass = MagicMock(
            return_value=(MANAGED_CHALLENGE_HTML, False, True),
        )
        handler._fetch_direct = MagicMock(
            return_value=(MANAGED_CHALLENGE_HTML, False, True),
        )

        result = handler._get_page_with_cf_bypass(
            url='https://javdb.com/?page=1',
            session=handler.session,
            use_cookie=False,
            use_proxy=False,
            module_name='spider',
            max_retries=1,
            proxies=None,
            use_proxy_pool_mode=False,
            proxy_name='P1',
            use_local_bypass=True,
            use_proxy_bypass=False,
        )

        assert result is None
        assert handler.last_site_challenge is True

    def test_get_page_resets_the_flag(self):
        handler = self._handler()
        handler.last_site_challenge = True
        handler._get_page_direct = MagicMock(return_value='<html></html>')

        handler.get_page('https://javdb.com/', use_proxy=False)

        assert handler.last_site_challenge is False


class TestZeroEntriesUnderChallengeFailsTheRun:
    """Without the ban, the run must still fail loudly instead of exiting 0."""

    def _report(self, *, rows, site_challenge_seen, skipped=0):
        import pytest

        from javdb.spider.runtime import report as report_mod
        from javdb.spider.runtime.context import SpiderRuntime

        runtime = SpiderRuntime()
        runtime.proxy.site_challenge_seen = site_challenge_seen

        kwargs = dict(
            runtime=runtime, phase_mode='all', parse_all=False,
            start_page=1, end_page=10, max_consecutive_empty=3,
            phase1_rows=[], phase2_rows=[], rows=rows,
            use_history_for_loading=True, ignore_history=False,
            skipped_history_count=skipped, failed_count=0,
            no_new_torrents_count=0, csv_path='out.csv', dry_run=True,
            use_history_for_saving=False, use_proxy=True,
            any_proxy_banned=False, any_proxy_banned_phase2=False,
        )
        return pytest, report_mod, kwargs

    def test_zero_entries_after_challenge_exits_2(self):
        pytest, report_mod, kwargs = self._report(rows=[], site_challenge_seen=True)

        with pytest.raises(SystemExit) as exc:
            report_mod.generate_summary_report(**kwargs)

        assert exc.value.code == 2

    def test_zero_entries_without_challenge_is_not_an_error(self):
        """An genuinely empty day must stay a success."""
        _pytest, report_mod, kwargs = self._report(rows=[], site_challenge_seen=False)

        report_mod.generate_summary_report(**kwargs)  # must not raise

    def test_challenge_but_entries_parsed_is_not_an_error(self):
        """Partial recovery — some proxies got through — stays a success."""
        row = {
            'href': '/v/a', 'video_code': 'ABC-1',
            'subtitle': '1', 'no_subtitle': '0',
            'hacked_subtitle': '0', 'hacked_no_subtitle': '0',
        }
        _pytest, report_mod, kwargs = self._report(
            rows=[row], site_challenge_seen=True,
        )

        report_mod.generate_summary_report(**kwargs)  # must not raise


class TestBypassUnreachableCache:
    @patch.object(RequestHandler, '_do_request')
    def test_connect_failure_disables_bypass_for_that_proxy(self, mock_do):
        mock_do.return_value = (None, requests.ConnectTimeout('timed out'))
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        proxies = {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'}

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', proxies, 'Proxy=P1', use_proxy_bypass=True,
        )
        assert mock_do.call_count == 1
        assert 'http://10.0.0.5:8000' in handler._bypass_unreachable

        # Second call must short-circuit instead of paying the connect timeout.
        html, success, challenge = handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', proxies, 'Proxy=P1', use_proxy_bypass=True,
        )
        assert mock_do.call_count == 1
        assert (html, success, challenge) == (None, False, False)

    @patch.object(RequestHandler, '_do_request')
    def test_connection_refused_also_disables_bypass(self, mock_do):
        """ConnectTimeout is a Timeout subclass — a plain refusal must count too."""
        mock_do.return_value = (None, requests.ConnectionError('connection refused'))
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        proxies = {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'}

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', proxies, 'Proxy=P1', use_proxy_bypass=True,
        )
        assert 'http://10.0.0.5:8000' in handler._bypass_unreachable

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', proxies, 'Proxy=P1', use_proxy_bypass=True,
        )
        assert mock_do.call_count == 1

    @patch.object(RequestHandler, '_do_request')
    def test_short_connect_timeout_is_used(self, mock_do):
        mock_do.return_value = ('<html>' + 'x' * 20000 + '</html>', None)
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', None, 'No proxy', force_local=True,
        )

        connect_timeout, read_timeout = mock_do.call_args.kwargs['timeout']
        assert connect_timeout <= 10
        assert read_timeout >= 30

    @patch.object(RequestHandler, '_do_request')
    def test_other_proxies_unaffected(self, mock_do):
        mock_do.return_value = (None, requests.ConnectTimeout('timed out'))
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))

        handler._fetch_with_cf_bypass(
            'https://javdb.com/', {'http': 'http://10.0.0.5:7890'},
            'Proxy=P1', use_proxy_bypass=True,
        )
        handler._fetch_with_cf_bypass(
            'https://javdb.com/', {'http': 'http://10.0.0.6:7890'},
            'Proxy=P2', use_proxy_bypass=True,
        )

        assert mock_do.call_count == 2
        assert handler._bypass_unreachable == {
            'http://10.0.0.5:8000', 'http://10.0.0.6:8000',
        }
