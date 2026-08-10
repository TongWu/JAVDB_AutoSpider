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
        assert handler._bypass_cache_key('10.0.0.5') in handler._bypass_unreachable

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
        assert handler._bypass_cache_key('10.0.0.5') in handler._bypass_unreachable

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
        assert set(handler._bypass_unreachable) == {
            handler._bypass_cache_key('10.0.0.5'),
            handler._bypass_cache_key('10.0.0.6'),
        }


class TestSiteWideChallengeIsNotReportedToTheCoordinator:
    """BFR-024 follow-up: the local soft-ban was exempted, the remote one wasn't.

    ``_record_cf_event`` fires two independent sinks. The penalty tracker is a
    pacing signal and must keep firing under a challenge. The ``on_cf_event``
    sink publishes to that proxy's coordinator Durable Object, which escalates
    ``CF_AUTO_BAN_THRESHOLD`` events into a ban — so under a site-wide wall
    every proxy crosses it at once and the whole pool is banned for a condition
    no proxy caused (ADR-043 D7 named this as out of scope).
    """

    def _handler(self, **cbs):
        return RequestHandler(config=RequestConfig(use_curl_cffi=False), **cbs)

    def test_site_wide_event_skips_the_coordinator(self):
        on_cf_event = MagicMock()
        on_site_challenge = MagicMock()
        handler = self._handler(
            on_cf_event=on_cf_event, on_site_challenge=on_site_challenge,
        )

        handler._record_cf_event('Singapore-ARM1', site_wide=True)

        on_cf_event.assert_not_called()
        # The observing proxy is forwarded: the Worker's breaker counts
        # *distinct* proxies, which is what separates a site-wide outage
        # from one unlucky IP.
        on_site_challenge.assert_called_once_with('Singapore-ARM1')

    def test_site_wide_event_still_paces_locally(self):
        tracker = MagicMock()
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False), penalty_tracker=tracker,
            on_cf_event=MagicMock(), on_site_challenge=MagicMock(),
        )

        handler._record_cf_event('Singapore-ARM1', site_wide=True)

        # Local pacing fires, but the tracker's own per-proxy coordinator
        # wire is suppressed — it is a second route to the same pool-wide ban.
        tracker.record_event.assert_called_once_with(publish_remote=False)

    def test_proxy_specific_event_still_reaches_the_coordinator(self):
        on_cf_event = MagicMock()
        on_site_challenge = MagicMock()
        handler = self._handler(
            on_cf_event=on_cf_event, on_site_challenge=on_site_challenge,
        )

        handler._record_cf_event('Singapore-ARM1')

        on_cf_event.assert_called_once_with('Singapore-ARM1')
        on_site_challenge.assert_not_called()

    def test_direct_challenge_routes_to_the_site_sink(self):
        """The path that actually fires in production: _get_page_direct."""
        on_cf_event = MagicMock()
        on_site_challenge = MagicMock()
        handler = self._handler(
            on_cf_event=on_cf_event, on_site_challenge=on_site_challenge,
        )
        handler._fetch_direct = MagicMock(
            return_value=(MANAGED_CHALLENGE_HTML, False, True),
        )

        result = handler._get_page_direct(
            url='https://javdb.com/?page=1',
            session=handler.session,
            use_cookie=False,
            module_name='spider',
            max_retries=1,
            proxies=None,
            use_proxy_pool_mode=False,
            proxy_name='Singapore-ARM1',
        )

        assert result is None
        assert handler.last_site_challenge is True
        on_site_challenge.assert_called_once_with('Singapore-ARM1')
        on_cf_event.assert_not_called()

    def test_site_sink_failure_does_not_break_the_fetch(self):
        handler = self._handler(on_site_challenge=MagicMock(side_effect=RuntimeError))

        handler._record_cf_event('Singapore-ARM1', site_wide=True)


class TestBypassFirstUnderSiteWideChallenge:
    """A walled site inverts the cascade: bypass leads, direct probes recovery.

    Leading with direct under a site-wide wall spends one guaranteed-failed
    attempt per proxy before the task reaches the only tier that can answer —
    ~30 wasted attempts per page across a 28-proxy pool, each one a CF event.
    """

    def _worker(self, *, site_challenge_active, always_bypass_time=None,
                cf_bypass_since=None):
        from javdb.spider.fetch.fetch_engine import _EngineWorker

        worker = _EngineWorker.__new__(_EngineWorker)
        worker.proxy_name = 'Singapore-ARM1'
        worker._bypass_first_streak = 0
        worker._cf_bypass_since = cf_bypass_since
        worker._runtime = MagicMock()
        worker._runtime.proxy.site_challenge_active = site_challenge_active
        worker._runtime.proxy.site_challenge_seen = site_challenge_active
        worker._runtime.proxy.always_bypass_time = always_bypass_time
        return worker

    def test_prefers_bypass_while_the_site_is_walled(self):
        worker = self._worker(site_challenge_active=True)
        assert worker._should_prefer_bypass() is True

    def test_prefers_direct_while_the_site_is_healthy(self):
        worker = self._worker(site_challenge_active=False)
        assert worker._should_prefer_bypass() is False

    def test_sticky_window_still_prefers_bypass_on_a_healthy_site(self):
        """--always-bypass-time is an independent trigger, not superseded."""
        import time

        worker = self._worker(
            site_challenge_active=False, always_bypass_time=0,
            cf_bypass_since=time.time(),
        )
        assert worker._should_prefer_bypass() is True

    def test_direct_success_clears_the_live_flag_but_not_the_latch(self):
        worker = self._worker(site_challenge_active=True)

        worker._mark_site_recovered()

        assert worker._runtime.proxy.site_challenge_active is False
        assert worker._runtime.proxy.site_challenge_seen is True

    def test_recovery_is_idempotent_when_already_healthy(self):
        worker = self._worker(site_challenge_active=False)

        worker._mark_site_recovered()

        assert worker._runtime.proxy.site_challenge_active is False

    def test_no_runtime_means_no_mode_switch(self):
        """The legacy no-runtime path must not crash on either hook."""
        worker = self._worker(site_challenge_active=False)
        worker._runtime = None

        assert worker._should_prefer_bypass() is False
        worker._mark_site_recovered()


class TestUnreachableBypassIsNotChargedToTheProxy:
    """A dead bypass service is a fault of that host's tier, not of the proxy.

    Squid answers 503 when nothing is listening on the tunnelled loopback port.
    That arrives as an HTTPError, not a ConnectionError — so the original
    ConnectionError-only guard never cached it, every page re-paid the round
    trip, and the exhausted cascades eventually raised ProxyBannedError. The
    coordinator then held that ban for days, long after the service came back.
    """

    def _handler(self):
        return RequestHandler(
            config=RequestConfig(use_curl_cffi=False, fallback_cooldown=0),
        )

    def _squid_503(self):
        response = requests.Response()
        response.status_code = 503
        return requests.HTTPError('503 Server Error', response=response)

    def test_proxy_reported_503_marks_the_bypass_unreachable(self):
        from javdb.infra.request import _is_bypass_unreachable_error

        assert _is_bypass_unreachable_error(self._squid_503()) is True

    def test_connect_failure_still_marks_the_bypass_unreachable(self):
        from javdb.infra.request import _is_bypass_unreachable_error

        assert _is_bypass_unreachable_error(requests.ConnectionError()) is True

    def test_a_404_does_not_mark_the_bypass_unreachable(self):
        """A 404 means the service answered — wrong dialect, not a dead host."""
        from javdb.infra.request import _is_bypass_unreachable_error

        response = requests.Response()
        response.status_code = 404
        assert _is_bypass_unreachable_error(
            requests.HTTPError('404', response=response),
        ) is False

    def test_no_error_does_not_mark_the_bypass_unreachable(self):
        from javdb.infra.request import _is_bypass_unreachable_error

        assert _is_bypass_unreachable_error(None) is False

    def test_503_populates_the_cache_and_skips_the_next_call(self):
        handler = self._handler()
        handler._do_request = MagicMock(return_value=(None, self._squid_503()))

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', None, 'Proxy=London-ARM1',
            force_local=True,
        )

        assert set(handler._bypass_unreachable) == {handler._bypass_cache_key(None)}

        # Second call short-circuits without touching the network at all.
        handler._do_request.reset_mock()
        result = handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', None, 'Proxy=London-ARM1',
            force_local=True,
        )

        assert result == (None, False, False)
        handler._do_request.assert_not_called()

    def test_exhausted_cascade_on_a_dead_bypass_raises_no_ban(self):
        """ban_threshold=1 would raise on the first exhaustion if charged."""
        handler = self._handler()
        handler.config.cf_bypass_ban_threshold = 1
        handler.refresh_bypass_cache = MagicMock(return_value=False)
        handler._do_request = MagicMock(return_value=(None, self._squid_503()))
        handler._fetch_direct = MagicMock(return_value=(None, False, False))

        result = handler._get_page_with_cf_bypass(
            url='https://javdb.com/?page=1',
            session=handler.session,
            use_cookie=False,
            use_proxy=False,
            module_name='spider',
            max_retries=1,
            proxies=None,
            use_proxy_pool_mode=False,
            proxy_name='London-ARM1',
            use_local_bypass=True,
            use_proxy_bypass=False,
        )

        assert result is None
        assert handler.cf_bypass_failure_count < 2


class TestSequentialPathBypassGate:
    """The sequential gate probed the wrong host and disabled the whole tier.

    ``is_cf_bypass_reachable`` defaults to ``127.0.0.1``. With a proxy pool the
    bypass service lives on each proxy host, so on a GitHub runner the probe
    always failed and ``--sequential`` runs silently lost CF bypass entirely.
    """

    def _effective(self, requested, **overrides):
        import javdb.spider.fetch.fallback as fallback

        defaults = {
            'CF_BYPASS_ENABLED': True,
            'CF_BYPASS_VIA_PROXY': False,
            'PROXY_MODE': 'pool',
        }
        defaults.update(overrides)
        with patch.multiple(fallback, **defaults):
            return fallback._effective_cf_bypass(requested)

    def test_pool_mode_does_not_probe_the_runners_loopback(self):
        import javdb.spider.fetch.fallback as fallback

        with patch.object(fallback, 'is_cf_bypass_reachable') as probe:
            assert self._effective(True, PROXY_MODE='pool') is True
            probe.assert_not_called()

    def test_via_proxy_does_not_probe_the_runners_loopback(self):
        import javdb.spider.fetch.fallback as fallback

        with patch.object(fallback, 'is_cf_bypass_reachable') as probe:
            assert self._effective(
                True, PROXY_MODE='none', CF_BYPASS_VIA_PROXY=True,
            ) is True
            probe.assert_not_called()

    def test_genuinely_local_deployment_still_probes(self):
        import javdb.spider.fetch.fallback as fallback

        with patch.object(
            fallback, 'is_cf_bypass_reachable', return_value=False,
        ) as probe:
            assert self._effective(True, PROXY_MODE='none') is False
            probe.assert_called_once()

    def test_globally_disabled_short_circuits(self):
        assert self._effective(True, CF_BYPASS_ENABLED=False) is False

    def test_not_requested_short_circuits(self):
        assert self._effective(False) is False


class TestBypassUnreachableCacheIsPerProxy:
    """Under CF_BYPASS_VIA_PROXY every proxy's bypass URL is 127.0.0.1.

    Keying the cache on that URL alone means one host's dead service disables
    the tier for the whole pool. Per-worker handlers own a single proxy and
    never collide; the global handler rotates the pool through one handler and
    does.
    """

    def _handler(self):
        return RequestHandler(config=RequestConfig(
            use_curl_cffi=False, cf_bypass_via_proxy=True,
        ))

    def test_two_proxies_share_a_loopback_url_but_not_a_key(self):
        handler = self._handler()

        assert handler.get_cf_bypass_service_url('10.0.0.5') == \
            handler.get_cf_bypass_service_url('10.0.0.6')
        assert handler._bypass_cache_key('10.0.0.5') != \
            handler._bypass_cache_key('10.0.0.6')

    def test_one_dead_host_does_not_disable_the_other(self):
        handler = self._handler()
        response = requests.Response()
        response.status_code = 503
        handler._do_request = MagicMock(
            return_value=(None, requests.HTTPError('503', response=response)),
        )
        proxies_a = {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'}
        proxies_b = {'http': 'http://10.0.0.6:7890', 'https': 'http://10.0.0.6:7890'}

        handler._fetch_with_cf_bypass(
            'https://javdb.com/', proxies_a, 'Proxy=A',
        )
        assert handler._bypass_cache_key('10.0.0.5') in handler._bypass_unreachable

        # B must still be attempted — its own service was never probed.
        handler._do_request.reset_mock()
        handler._fetch_with_cf_bypass(
            'https://javdb.com/', proxies_b, 'Proxy=B',
        )
        handler._do_request.assert_called_once()


class TestSiteRecoveryProbe:
    """A run whose bypass keeps working must still notice the wall coming down."""

    def _worker(self):
        from javdb.spider.fetch.fetch_engine import _EngineWorker

        worker = _EngineWorker.__new__(_EngineWorker)
        worker.proxy_name = 'Singapore-ARM1'
        worker._cf_bypass_since = None
        worker._bypass_first_streak = 0
        worker._runtime = MagicMock()
        worker._runtime.proxy.site_challenge_active = True
        worker._runtime.proxy.always_bypass_time = None
        return worker

    def test_every_nth_task_leads_with_direct(self):
        from javdb.spider.fetch.fetch_engine import SITE_RECOVERY_PROBE_INTERVAL

        worker = self._worker()
        decisions = [
            worker._should_prefer_bypass()
            for _ in range(SITE_RECOVERY_PROBE_INTERVAL * 2)
        ]

        assert decisions.count(False) == 2
        assert decisions[SITE_RECOVERY_PROBE_INTERVAL - 1] is False
        assert decisions[0] is True

    def test_streak_resets_once_the_site_recovers(self):
        worker = self._worker()
        worker._should_prefer_bypass()
        worker._runtime.proxy.site_challenge_active = False

        assert worker._should_prefer_bypass() is False
        assert worker._bypass_first_streak == 0


class TestUnreachableWarningNamesTheStatus:
    """503 and 504 want opposite fixes, so the status has to reach INFO.

    503 = the proxy could not open the connection (service refusing);
    504 = it connected and the service never answered (solver overloaded).
    Run 31194312995 logged 14 of these and none of them said which.
    """

    def _http_error(self, status):
        response = requests.Response()
        response.status_code = status
        return requests.HTTPError(str(status), response=response)

    def test_status_is_included_for_an_http_error(self):
        from javdb.infra.request import _describe_bypass_error

        assert _describe_bypass_error(self._http_error(503)) == 'HTTPError 503'
        assert _describe_bypass_error(self._http_error(504)) == 'HTTPError 504'

    def test_falls_back_to_the_exception_name(self):
        from javdb.infra.request import _describe_bypass_error

        assert _describe_bypass_error(requests.ConnectionError()) == 'ConnectionError'
        assert _describe_bypass_error(None) == 'NoneType'

    def test_the_warning_carries_the_status(self, caplog):
        import logging

        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        handler._do_request = MagicMock(return_value=(None, self._http_error(503)))

        with caplog.at_level(logging.WARNING):
            handler._fetch_with_cf_bypass(
                'https://javdb.com/', None, 'Proxy=Vinhedo-ARM1', force_local=True,
            )

        assert any('HTTPError 503' in r.message for r in caplog.records)


class TestBypassUnreachableExpiresAndIsReProbed:
    """BFR-025 follow-up: the "bypass is down" cache is a TTL, not a latch.

    Forensics on run 31194312995 showed these 5xx failures flap — 7 of the 9
    hosts that returned one had both succeeded before and succeeded after, in
    the same run. Latching a host off for the whole phase retires a service
    that was only briefly unavailable.
    """

    def _squid_503(self):
        response = requests.Response()
        response.status_code = 503
        return requests.HTTPError('503', response=response)

    def _handler(self):
        return RequestHandler(config=RequestConfig(use_curl_cffi=False))

    def test_a_host_is_skipped_while_the_window_is_open(self):
        handler = self._handler()
        handler._do_request = MagicMock(return_value=(None, self._squid_503()))

        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', None, 'Proxy=P1', force_local=True,
        )
        handler._do_request.reset_mock()
        result = handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', None, 'Proxy=P1', force_local=True,
        )

        assert handler._do_request.call_count == 0
        assert result == (None, False, False)

    def test_the_host_is_re_probed_once_the_window_lapses(self):
        import javdb.infra.request as request_mod

        handler = self._handler()
        handler._do_request = MagicMock(return_value=(None, self._squid_503()))
        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', None, 'Proxy=P1', force_local=True,
        )

        # Rewind the mark past the TTL rather than sleeping through it.
        key = handler._bypass_cache_key(None)
        handler._bypass_unreachable[key] -= request_mod.BYPASS_UNREACHABLE_TTL + 1

        handler._do_request.reset_mock()
        handler._do_request.return_value = ('<html>' + 'x' * 20000 + '</html>', None)
        html, success, _ = handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', None, 'Proxy=P1', force_local=True,
        )

        assert handler._do_request.call_count == 1
        assert success is True
        assert html is not None
        # A recovered host must not stay in the cache.
        assert key not in handler._bypass_unreachable

    def test_a_failed_re_probe_reopens_the_window(self):
        import javdb.infra.request as request_mod

        handler = self._handler()
        handler._do_request = MagicMock(return_value=(None, self._squid_503()))
        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=1', None, 'Proxy=P1', force_local=True,
        )

        key = handler._bypass_cache_key(None)
        handler._bypass_unreachable[key] -= request_mod.BYPASS_UNREACHABLE_TTL + 1
        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=2', None, 'Proxy=P1', force_local=True,
        )

        handler._do_request.reset_mock()
        handler._fetch_with_cf_bypass(
            'https://javdb.com/?page=3', None, 'Proxy=P1', force_local=True,
        )
        assert handler._do_request.call_count == 0

    def test_ban_accounting_is_skipped_only_while_the_window_is_open(self):
        import javdb.infra.request as request_mod

        handler = self._handler()
        key = handler._bypass_cache_key(None)

        assert handler._bypass_is_unreachable(key) is False
        handler._mark_bypass_unreachable(key)
        assert handler._bypass_is_unreachable(key) is True

        handler._bypass_unreachable[key] -= request_mod.BYPASS_UNREACHABLE_TTL + 1
        assert handler._bypass_is_unreachable(key) is False


class TestBypassLogsTheResolvedPort:
    """A host remapped by CF_BYPASS_PORT_MAP must be reported at its own port."""

    def _handler(self):
        return RequestHandler(config=RequestConfig(
            use_curl_cffi=False,
            cf_bypass_service_port=8000,
            cf_bypass_port_map={'10.0.0.5': 8002},
        ))

    def test_the_mapped_port_is_used_in_the_masked_base(self):
        handler = self._handler()

        assert handler._masked_bypass_base('10.0.0.5').endswith(':8002')
        assert handler._masked_bypass_base('10.0.0.9').endswith(':8000')
        assert handler._masked_bypass_base(None) == 'http://127.0.0.1:8000'

    def test_the_masked_base_does_not_leak_the_proxy_ip(self):
        handler = self._handler()

        assert '10.0.0.5' not in handler._masked_bypass_base('10.0.0.5')

    def test_the_unreachable_warning_names_the_mapped_port(self, caplog):
        import logging

        response = requests.Response()
        response.status_code = 503
        handler = self._handler()
        handler._do_request = MagicMock(
            return_value=(None, requests.HTTPError('503', response=response))
        )

        with caplog.at_level(logging.WARNING):
            handler._fetch_with_cf_bypass(
                'https://javdb.com/',
                {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'},
                'Proxy=P1', use_proxy_bypass=True,
            )

        warnings = [r.message for r in caplog.records]
        assert any(':8002' in m for m in warnings)
        assert not any(':8000' in m for m in warnings)


class TestSiteChallengeIsReportedAsItsOwnKind:
    """BFR-025 / ADR-043 D7: the Worker needs the signal, just not as ``cf``.

    Suppressing the report entirely — the first cut of this fix — starves the
    RunnerRegistry circuit breaker, which exists precisely to notice a
    site-wide wall. ``site_challenge`` carries the observation without
    touching the per-proxy counters that drive the auto-ban.
    """

    def test_the_kind_is_accepted_by_the_client(self):
        from javdb.proxy.coordinator.proxy_coordinator_client import (
            _VALID_REPORT_KINDS, _validate_kind,
        )

        assert 'site_challenge' in _VALID_REPORT_KINDS
        assert _validate_kind('site_challenge') == 'site_challenge'

    def test_the_observing_proxy_is_forwarded_to_the_callback(self):
        """The breaker counts distinct proxies, so the name has to survive."""
        seen = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_site_challenge=lambda proxy_name=None: seen.append(proxy_name),
            on_cf_event=lambda _proxy_name=None: seen.append('WRONG'),
        )

        handler._record_cf_event(proxy_name='London-ARM1', site_wide=True)

        assert seen == ['London-ARM1']

    def test_a_proxy_specific_event_still_goes_to_on_cf_event(self):
        site_wide, per_proxy = [], []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_site_challenge=lambda proxy_name=None: site_wide.append(proxy_name),
            on_cf_event=lambda proxy_name=None: per_proxy.append(proxy_name),
        )

        handler._record_cf_event(proxy_name='London-ARM1')

        assert per_proxy == ['London-ARM1']
        assert site_wide == []

    def test_a_raising_callback_does_not_break_the_request(self):
        def _boom(proxy_name=None):
            raise RuntimeError('coordinator down')

        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_site_challenge=_boom,
        )

        handler._record_cf_event(proxy_name='P1', site_wide=True)   # must not raise


class TestPenaltyTrackerDoesNotLeakSiteWideEventsUpstream:
    """The tracker has its own coordinator wire — a second path to the same bug.

    ``PenaltyTracker.record_event()`` publishes ``cf`` to the proxy's DO when
    a coordinator is injected. That call sits *before* the ``site_wide``
    early-out in ``_record_cf_event``, so a site-wide wall would reach every
    DO through it and ban the pool exactly as BFR-024 described. No production
    tracker is wired to a coordinator today, which is precisely why this needs
    a test rather than a comment.
    """

    def _tracker(self):
        from javdb.spider.runtime.sleep import PenaltyTracker

        coordinator = MagicMock()
        tracker = PenaltyTracker(coordinator=coordinator, proxy_id='proxy-a')
        return tracker, coordinator

    def test_a_site_wide_event_does_not_reach_the_proxy_do(self):
        tracker, coordinator = self._tracker()
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            penalty_tracker=tracker,
            on_site_challenge=MagicMock(),
        )

        handler._record_cf_event(proxy_name='proxy-a', site_wide=True)

        coordinator.report_async.assert_not_called()

    def test_local_pacing_still_fires_for_a_site_wide_event(self):
        """Backing off while CF pushes back is correct however it arose."""
        tracker, _ = self._tracker()
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            penalty_tracker=tracker,
            on_site_challenge=MagicMock(),
        )

        handler._record_cf_event(proxy_name='proxy-a', site_wide=True)

        assert len(tracker._events) == 1

    def test_a_proxy_specific_event_still_reaches_the_proxy_do(self):
        tracker, coordinator = self._tracker()
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            penalty_tracker=tracker,
        )

        handler._record_cf_event(proxy_name='proxy-a')

        coordinator.report_async.assert_called_once_with('proxy-a', 'cf')

    def test_the_default_still_publishes(self):
        """Every other caller passes no flag and must be unaffected."""
        tracker, coordinator = self._tracker()

        tracker.record_event()

        coordinator.report_async.assert_called_once_with('proxy-a', 'cf')
