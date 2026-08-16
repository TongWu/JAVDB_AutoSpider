"""Fail-closed proxy contract for fetch_javdb_html.

request.py's get_page raises ProxyExhaustedError rather than exposing the real
exit IP when a proxy is demanded. fetch_javdb_html must honour that: under proxy
mode it must never fall back to the direct simple-fetch path (which would leak
the IP). These pin that contract.
"""
import pytest
from fastapi import HTTPException

from apps.api.services import javdb_fetch_service as fetch
from javdb.infra.request import ProxyExhaustedError


class _Handler:
    def __init__(self, proxy_pool=object(), get_page=None):
        self.proxy_pool = proxy_pool
        self._get_page = get_page

    def get_page(self, **kwargs):
        if self._get_page is not None:
            return self._get_page()
        return None


def _wire(monkeypatch, *, cfg, handler, simple):
    monkeypatch.setattr(fetch, "validate_javdb_url_or_422", lambda url: None)
    monkeypatch.setattr(fetch.config_service, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(fetch, "new_request_handler", lambda c: handler)
    monkeypatch.setattr(fetch, "simple_fetch_javdb_html", simple)


def test_proxy_required_with_no_pool_refuses_direct_fallback(monkeypatch):
    """Pool failed to construct (None) under proxy mode -> 502, no direct fetch."""
    called = {"simple": False}

    def simple(*a, **k):
        called["simple"] = True
        return "<html>direct-leak</html>"

    _wire(monkeypatch, cfg={"PROXY_MODE": "pool"}, handler=_Handler(proxy_pool=None), simple=simple)
    with pytest.raises(HTTPException) as exc:
        fetch.fetch_javdb_html("https://javdb.com/v/abc", use_proxy=True)
    assert exc.value.status_code == 502
    assert called["simple"] is False


def test_proxy_required_get_page_exhausted_refuses_direct_fallback(monkeypatch):
    """get_page raises ProxyExhaustedError -> 502, never reaches simple-fetch."""
    called = {"simple": False}

    def simple(*a, **k):
        called["simple"] = True
        return "<html>direct-leak</html>"

    def boom():
        raise ProxyExhaustedError(proxy_name="p1", reason="all in cooldown")

    _wire(
        monkeypatch,
        cfg={"PROXY_MODE": "single"},
        handler=_Handler(get_page=boom),
        simple=simple,
    )
    with pytest.raises(HTTPException) as exc:
        fetch.fetch_javdb_html("https://javdb.com/v/abc", use_proxy=True)
    assert exc.value.status_code == 502
    assert called["simple"] is False


def test_no_proxy_still_allows_direct_fallback(monkeypatch):
    """use_proxy=False -> the direct simple-fetch fallback is intended and runs."""
    _wire(
        monkeypatch,
        cfg={"PROXY_MODE": "pool"},
        handler=_Handler(proxy_pool=None, get_page=lambda: None),
        simple=lambda *a, **k: "<html>ok</html>",
    )
    result = fetch.fetch_javdb_html("https://javdb.com/v/abc", use_proxy=False)
    assert result == "<html>ok</html>"


def test_proxy_required_success_returns_without_fallback(monkeypatch):
    """A good proxy response returns directly and never touches simple-fetch."""
    called = {"simple": False}

    def simple(*a, **k):
        called["simple"] = True
        return "<html>x</html>"

    _wire(
        monkeypatch,
        cfg={"PROXY_MODE": "pool"},
        handler=_Handler(get_page=lambda: "<html><body>real page</body></html>"),
        simple=simple,
    )
    result = fetch.fetch_javdb_html("https://javdb.com/v/abc", use_proxy=True)
    assert "real page" in result
    assert called["simple"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/v/abc",             # plain foreign host
        "https://javdb.com.evil.com/v/abc",   # look-alike suffix
        "https://javdb.com@evil.com/v/abc",   # userinfo trick
        "https://127.0.0.1/v/abc",            # loopback
        "https://169.254.169.254/latest/",    # cloud metadata
        "file:///etc/passwd",                 # non-http scheme
    ],
)
@pytest.mark.parametrize("use_proxy", [False, True])
def test_hosts_outside_the_javdb_allowlist_never_reach_an_outbound_call(
    monkeypatch, url, use_proxy,
):
    """SSRF guard: every fetch path in this module validates the URL against the
    JavDB host allowlist *before* any request is built, so a caller-supplied URL
    can never redirect the fetch at another host. Neither the request-handler
    path nor the simple-fetch path may be entered, whether or not a proxy was
    requested."""
    reached = {"handler": False, "simple": False}

    def handler(_cfg):
        reached["handler"] = True
        return _Handler()

    def simple(*a, **k):
        reached["simple"] = True
        return "<html>leak</html>"

    monkeypatch.setattr(fetch.config_service, "load_runtime_config", lambda: {})
    monkeypatch.setattr(fetch, "new_request_handler", handler)
    monkeypatch.setattr(fetch, "simple_fetch_javdb_html", simple)

    with pytest.raises(HTTPException) as exc:
        fetch.fetch_javdb_html(url, use_proxy=use_proxy)

    assert exc.value.status_code == 422
    assert reached == {"handler": False, "simple": False}
