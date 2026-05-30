"""Unit tests for backfill_movie_metadata._process_href / _detail_url.

Regression guards for the metadata-backfill fetch/parse fixes:

1. JavDB detail pages sit behind Cloudflare — a no-bypass fetch returns an
   empty body, so the proxy path must enable ``use_cf_bypass``.
2. ``MovieHistory.Href`` is stored as an absolute URL; ``base_url + href``
   produced a doubled ``https://..https://..`` URL that never resolved.
3. ``MovieDetail.parse_success`` only reports whether a magnets section was
   found (often login-gated). Metadata must be written whenever core fields
   (title / video_code) were parsed, regardless of magnets.
"""

import types

import javdb.migrations.tools.backfill_movie_metadata as bm


def _patch_get_page(monkeypatch, return_html, recorder):
    def _fake_get_page(url, session=None, use_proxy=False,
                       module_name='unknown', use_cf_bypass=False, **_kw):
        recorder['url'] = url
        recorder['use_proxy'] = use_proxy
        recorder['use_cf_bypass'] = use_cf_bypass
        return return_html

    monkeypatch.setattr(bm.spider_state, 'get_page', _fake_get_page)


def _patch_parse(monkeypatch, *, video_code='ABC-123', title='t',
                 parse_success=True):
    detail = types.SimpleNamespace(
        video_code=video_code, title=title, parse_success=parse_success,
    )
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)
    return detail


def test_process_href_enables_cf_bypass_when_proxied(monkeypatch):
    """With a proxy, the fetch must request CF bypass."""
    recorder = {}
    _patch_get_page(monkeypatch, '<html>ok</html>', recorder)
    _patch_parse(monkeypatch)
    upserts = {}
    monkeypatch.setattr(
        bm.MetadataRepo, 'upsert',
        lambda self, href, detail: upserts.setdefault('href', href),
    )

    result = bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=True, dry_run=False,
    )

    assert recorder['use_cf_bypass'] is True
    assert recorder['use_proxy'] is True
    assert result.status == 'ok'
    assert upserts['href'] == '/v/abc'


def test_process_href_no_proxy_stays_direct(monkeypatch):
    """The --no-proxy debug path must not request CF bypass (no local service)."""
    recorder = {}
    _patch_get_page(monkeypatch, '<html>ok</html>', recorder)
    _patch_parse(monkeypatch)
    monkeypatch.setattr(bm.MetadataRepo, 'upsert', lambda self, href, detail: None)

    result = bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=False, dry_run=False,
    )

    assert recorder['use_cf_bypass'] is False
    assert result.status == 'ok'


def test_process_href_empty_response_is_fetch_failed(monkeypatch):
    """An empty body (Cloudflare challenge) surfaces as fetch_failed."""
    recorder = {}
    _patch_get_page(monkeypatch, '', recorder)

    result = bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=True, dry_run=False,
    )

    assert result.status == 'fetch_failed'
    assert recorder['use_cf_bypass'] is True


def test_process_href_writes_metadata_without_magnets(monkeypatch):
    """parse_success=False (no magnets section) must NOT block the upsert when
    core metadata was parsed — that gate is magnet-specific, not metadata."""
    recorder = {}
    _patch_get_page(monkeypatch, '<html>ok</html>', recorder)
    _patch_parse(monkeypatch, video_code='ABC-123', title='', parse_success=False)
    upserts = {}
    monkeypatch.setattr(
        bm.MetadataRepo, 'upsert',
        lambda self, href, detail: upserts.setdefault('href', href),
    )

    result = bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=True, dry_run=False,
    )

    assert result.status == 'ok'
    assert upserts['href'] == '/v/abc'


def test_process_href_empty_metadata_is_parse_failed(monkeypatch):
    """A fetched body with neither title nor video_code is parse_failed."""
    recorder = {}
    _patch_get_page(monkeypatch, '<html>challenge</html>', recorder)
    _patch_parse(monkeypatch, video_code='', title='', parse_success=False)

    result = bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=True, dry_run=False,
    )

    assert result.status == 'parse_failed'


def test_detail_url_absolute_href_not_doubled():
    """Stored Href is absolute — must be used verbatim, not prefixed."""
    assert (
        bm._detail_url('https://javdb.com/v/pNBkZ', 'https://javdb.com')
        == 'https://javdb.com/v/pNBkZ'
    )
    # Regression: the old `base_url + href` produced this doubled URL.
    assert 'comhttps://' not in bm._detail_url(
        'https://javdb.com/v/pNBkZ', 'https://javdb.com'
    )


def test_detail_url_relative_href_prefixed():
    """Legacy/relative href still resolves against base_url, no double slash."""
    assert (
        bm._detail_url('/v/abc', 'https://javdb.com')
        == 'https://javdb.com/v/abc'
    )
    assert (
        bm._detail_url('v/abc', 'https://javdb.com')
        == 'https://javdb.com/v/abc'
    )
