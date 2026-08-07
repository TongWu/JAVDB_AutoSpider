"""Login-path Cloudflare handling — BFR-025 follow-up."""

from unittest.mock import MagicMock, patch

import javdb.spider.auth.login as login


MANAGED_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '</head><body></body></html>'
)


class TestLoginSeesTheManagedChallenge:
    """The login path kept a second, stricter copy of the predicate.

    It required 'Security Verification' AND 'turnstile'; the managed-challenge
    page carries neither, so login could not see the wall the spider was
    hitting (BFR-024 fixed only the fetch layer).
    """

    def _response(self, status, text, server=''):
        response = MagicMock()
        response.status_code = status
        response.text = text
        response.headers = {'server': server}
        return response

    def test_managed_challenge_with_200_is_detected(self):
        assert login._is_cloudflare_challenge(
            self._response(200, MANAGED_CHALLENGE_HTML),
        ) is True

    def test_legacy_turnstile_page_still_detected(self):
        html = '<html><title>Security Verification</title><div class="cf-turnstile"></div></html>'
        assert login._is_cloudflare_challenge(self._response(200, html)) is True

    def test_403_from_cloudflare_still_detected(self):
        assert login._is_cloudflare_challenge(
            self._response(403, 'blocked', server='cloudflare'),
        ) is True

    def test_ordinary_page_is_not_a_challenge(self):
        html = '<html><body><div class="movie-list"></div></body></html>'
        assert login._is_cloudflare_challenge(self._response(200, html)) is False


class TestWarmupHonoursTheProxyTunnel:
    """CF_BYPASS_VIA_PROXY rewrites the bypass URL to 127.0.0.1.

    Dialling that directly reaches the *runner's* loopback, where nothing
    listens — so the warmup could only ever fail once the flag was turned on,
    and blocked for the full 120s timeout doing it.
    """

    def _handler(self, via_proxy):
        from javdb.infra.request import RequestConfig, RequestHandler

        return RequestHandler(config=RequestConfig(
            cf_bypass_enabled=True, cf_bypass_via_proxy=via_proxy,
            use_curl_cffi=False,
        ))

    def test_request_is_tunnelled_through_the_proxy(self):
        handler = self._handler(via_proxy=True)
        proxies = {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'}

        with patch.object(login.requests, 'get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200, content=b'x' * 2000)
            assert login._attempt_cf_warmup(
                handler, 'https://javdb.com/', proxies,
            ) is True

        url, kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        assert url.startswith('http://127.0.0.1:8000/html?url=')
        assert kwargs['proxies'] == proxies
        # Split timeout, not the old scalar 120.
        assert isinstance(kwargs['timeout'], tuple)

    def test_direct_dial_when_the_tunnel_is_off(self):
        handler = self._handler(via_proxy=False)
        proxies = {'http': 'http://10.0.0.5:7890', 'https': 'http://10.0.0.5:7890'}

        with patch.object(login.requests, 'get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200, content=b'x' * 2000)
            login._attempt_cf_warmup(handler, 'https://javdb.com/', proxies)

        url, kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        assert url.startswith('http://10.0.0.5:8000/html?url=')
        assert kwargs['proxies'] is None
