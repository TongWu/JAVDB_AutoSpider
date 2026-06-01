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

import logging
import types

import javdb.migrations.tools.backfill_movie_metadata as bm


def _patch_get_page(monkeypatch, return_html, recorder):
    def _fake_get_page(url, session=None, use_proxy=False,
                       module_name='unknown', use_cf_bypass=False,
                       use_cookie=False, **_kw):
        recorder['url'] = url
        recorder['use_proxy'] = use_proxy
        recorder['use_cf_bypass'] = use_cf_bypass
        recorder['use_cookie'] = use_cookie
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


def test_process_href_fetch_authenticates_with_session_cookie(monkeypatch):
    """The fetch must request the login cookie (use_cookie=True) so login-gated
    movies render metadata instead of a login wall."""
    recorder = {}
    _patch_get_page(monkeypatch, '<html>ok</html>', recorder)
    _patch_parse(monkeypatch)
    monkeypatch.setattr(bm.MetadataRepo, 'upsert', lambda self, href, detail: None)

    bm._process_href(
        '/v/abc', 'https://javdb.com/v/abc', session=None,
        use_proxy=True, dry_run=False,
    )

    assert recorder['use_cookie'] is True


def test_process_href_login_wall_is_login_required(monkeypatch):
    """A login wall (missing/expired cookie) is reported as login_required,
    distinct from parse_failed, and short-circuits before parse/upsert."""
    recorder = {}
    # Real login page: <title> contains 登入 — is_login_page() detects it.
    _patch_get_page(
        monkeypatch,
        '<html><head><title>登入 - JavDB</title></head><body></body></html>',
        recorder,
    )
    called = {'parsed': False, 'upserted': False}

    def _boom_parse(_html):
        called['parsed'] = True
        raise AssertionError('parse_detail_page must not run on a login wall')

    monkeypatch.setattr(bm, 'parse_detail_page', _boom_parse)
    monkeypatch.setattr(
        bm.MetadataRepo, 'upsert',
        lambda self, href, detail: called.__setitem__('upserted', True),
    )

    result = bm._process_href(
        '/v/a2nq3', 'https://javdb.com/v/a2nq3', session=None,
        use_proxy=True, dry_run=False,
    )

    assert result.status == 'login_required'
    assert called['parsed'] is False
    assert called['upserted'] is False


def _run_backfill_with_statuses(monkeypatch, statuses):
    """Drive run_backfill_metadata over len(statuses) hrefs with _process_href
    stubbed to return each status in turn, and all I/O (DB, proxy pool, request
    handler, inter-href sleep) mocked out. Returns the exit code."""
    hrefs = [f'/v/{i}' for i in range(len(statuses))]
    monkeypatch.setattr(bm, '_load_hrefs_without_metadata', lambda only=None: list(hrefs))
    monkeypatch.setattr(bm.spider_state, 'setup_proxy_pool', lambda **_kw: None)
    monkeypatch.setattr(bm.spider_state, 'initialize_request_handler', lambda *a, **k: None)
    monkeypatch.setattr(bm.time, 'sleep', lambda *_a, **_k: None)
    statuses_iter = iter(statuses)
    monkeypatch.setattr(
        bm, '_process_href',
        lambda href, *a, **k: bm.BackfillResult(href, next(statuses_iter), 'stub'),
    )
    args = types.SimpleNamespace(
        hrefs='', shuffle=False, limit=0, limit_per_worker=0,
        use_proxy=False, dry_run=False,
    )
    return bm.run_backfill_metadata(args)


def test_run_backfill_login_required_is_non_fatal(monkeypatch, caplog):
    """login_required is counted as login_gated, not failed: the job still
    returns 0 and warns the operator to refresh the cookie."""
    with caplog.at_level(logging.WARNING):
        rc = _run_backfill_with_statuses(monkeypatch, ['login_required', 'login_required'])

    assert rc == 0                                    # non-fatal ⇒ failed == 0
    assert '2 href(s) require login' in caplog.text   # login_gated == 2


def test_run_backfill_parse_failed_is_fatal(monkeypatch):
    """A genuine parse_failed still fails the job (return 1), proving
    login_required is classified distinctly from a hard failure."""
    assert _run_backfill_with_statuses(monkeypatch, ['parse_failed']) == 1


def test_run_backfill_partial_failure_is_non_fatal(monkeypatch):
    """A bounded backfill batch may complete with some unparseable pages.
    Successful rows should be kept and the migration step should continue."""
    assert _run_backfill_with_statuses(monkeypatch, ['ok', 'parse_failed']) == 0


def test_run_backfill_login_required_does_not_mask_real_failure(monkeypatch):
    """A login_required alongside a real failure must not rescue the exit code:
    login_gated is counted apart, but the failed href still returns 1."""
    assert _run_backfill_with_statuses(monkeypatch, ['login_required', 'parse_failed']) == 1


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


def test_backfill_metadata_parse_returns_detail_when_video_code_present(monkeypatch):
    detail = types.SimpleNamespace(video_code='ABC-123', title='')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)

    result = bm._backfill_metadata_parse('<html></html>', types.SimpleNamespace())

    assert result is detail


def test_backfill_metadata_parse_returns_detail_when_title_only(monkeypatch):
    detail = types.SimpleNamespace(video_code='', title='A Title')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)

    result = bm._backfill_metadata_parse('<html></html>', types.SimpleNamespace())

    assert result is detail


def test_backfill_metadata_parse_returns_none_when_metadata_missing(monkeypatch):
    detail = types.SimpleNamespace(video_code='', title='')
    monkeypatch.setattr(bm, 'parse_detail_page', lambda _html: detail)

    result = bm._backfill_metadata_parse('<html></html>', types.SimpleNamespace())

    assert result is None


def test_parallel_backfill_engine_receives_base_sleep_range(monkeypatch):
    """Parallel workers receive the unscaled base range; each worker scales it."""
    import javdb.spider.fetch.fetch_engine as fetch_engine_module
    import javdb.spider.runtime.config as runtime_config

    monkeypatch.setattr(bm, '_load_hrefs_without_metadata', lambda only=None: ['/v/abc'])
    monkeypatch.setattr(
        runtime_config,
        'PROXY_POOL',
        [{'name': 'proxy-a'}, {'name': 'proxy-b'}],
    )
    monkeypatch.setattr(bm.spider_state, 'setup_proxy_pool', lambda **_kw: None)
    monkeypatch.setattr(bm.spider_state, 'initialize_request_handler', lambda: None)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'base_min', 7.0)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'base_max', 17.0)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'sleep_min', 77.0)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'sleep_max', 177.0)

    def _fake_apply_volume_multiplier(total, num_workers=1, **_kw):
        assert total == 1
        assert num_workers == 2
        bm.movie_sleep_mgr.sleep_min = 77.0
        bm.movie_sleep_mgr.sleep_max = 177.0

    monkeypatch.setattr(
        bm.movie_sleep_mgr,
        'apply_volume_multiplier',
        _fake_apply_volume_multiplier,
    )

    captured = {}

    class _FakeEngine:
        def start(self):
            pass

        def submit(self, url, *, entry_index='', meta=None, priority=0):
            captured['submitted'] = {
                'url': url,
                'entry_index': entry_index,
                'meta': meta,
                'priority': priority,
            }

        def mark_done(self):
            pass

        def results(self):
            yield types.SimpleNamespace(
                task=types.SimpleNamespace(
                    meta={'href': '/v/abc'},
                    entry_index='meta-1/1',
                    url='https://javdb.com/v/abc',
                ),
                success=True,
                data=types.SimpleNamespace(video_code='ABC-123', title='A Title'),
                error='',
            )

        def shutdown(self, *, timeout=10):
            captured['shutdown_timeout'] = timeout
            return []

    def _fake_simple(**kwargs):
        captured['simple_kwargs'] = kwargs
        return _FakeEngine()

    monkeypatch.setattr(fetch_engine_module.FetchEngine, 'simple', _fake_simple)

    args = types.SimpleNamespace(
        hrefs='',
        shuffle=False,
        limit=0,
        limit_per_worker=0,
        use_proxy=True,
        dry_run=True,
    )

    assert bm.run_backfill_metadata(args) == 0

    assert captured['simple_kwargs']['sleep_min'] == 7.0
    assert captured['simple_kwargs']['sleep_max'] == 17.0
    assert captured['submitted']['url'] == 'https://javdb.com/v/abc'


def test_parallel_backfill_submit_interrupt_drains_remaining(monkeypatch):
    import javdb.spider.fetch.fetch_engine as fetch_engine_module
    import javdb.spider.runtime.config as runtime_config

    monkeypatch.setattr(bm, '_load_hrefs_without_metadata', lambda only=None: ['/v/abc'])
    monkeypatch.setattr(runtime_config, 'PROXY_POOL', [{'name': 'proxy-a'}])
    monkeypatch.setattr(bm.spider_state, 'setup_proxy_pool', lambda **_kw: None)
    monkeypatch.setattr(bm.spider_state, 'initialize_request_handler', lambda: None)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'apply_volume_multiplier',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'base_min', 7.0)
    monkeypatch.setattr(bm.movie_sleep_mgr, 'base_max', 17.0)

    captured = {'calls': []}

    class _FakeEngine:
        def start(self):
            captured['calls'].append('start')

        def submit(self, url, *, entry_index='', meta=None, priority=0):
            captured['calls'].append(('submit', url, entry_index, meta))
            raise KeyboardInterrupt()

        def mark_done(self):
            captured['calls'].append('mark_done')

        def results(self):
            captured['calls'].append('results')
            return iter([])

        def shutdown(self, *, timeout=10):
            captured['calls'].append(('shutdown', timeout))
            return [types.SimpleNamespace(url='orphaned')]

        def drain_remaining(self):
            captured['calls'].append('drain_remaining')
            yield types.SimpleNamespace(
                task=types.SimpleNamespace(
                    meta={'href': '/v/drained'},
                    entry_index='meta-1/1',
                    url='https://javdb.com/v/drained',
                ),
                success=True,
                data=types.SimpleNamespace(video_code='ABC-123', title='A Title'),
                error='',
            )

    monkeypatch.setattr(
        fetch_engine_module.FetchEngine,
        'simple',
        lambda **_kwargs: _FakeEngine(),
    )
    upserts = {}
    monkeypatch.setattr(
        bm.MetadataRepo,
        'upsert',
        lambda self, href, detail: upserts.update(href=href, detail=detail),
    )

    args = types.SimpleNamespace(
        hrefs='',
        shuffle=False,
        limit=0,
        limit_per_worker=0,
        use_proxy=True,
        dry_run=False,
    )

    assert bm.run_backfill_metadata(args) == 130

    assert ('shutdown', 30) in captured['calls']
    assert 'drain_remaining' in captured['calls']
    assert 'mark_done' not in captured['calls']
    assert 'results' not in captured['calls']
    assert upserts['href'] == '/v/drained'


def _make_metadata_result(*, href='/v/abc', success=True, detail=None,
                          error='', worker_name='proxy-1'):
    if detail is None:
        detail = types.SimpleNamespace(video_code='ABC-123', title='A Title')
    return types.SimpleNamespace(
        task=types.SimpleNamespace(meta={'href': href}, entry_index='meta-1/1'),
        success=success,
        data=detail,
        error=error,
        worker_name=worker_name,
        used_cf=False,
    )


def test_apply_metadata_result_success_writes_via_metadata_repo_upsert(monkeypatch):
    detail = types.SimpleNamespace(video_code='ABC-123', title='A Title')
    result = _make_metadata_result(detail=detail)
    recorded = {}

    def _fake_upsert(self, href, parsed_detail):
        recorded['href'] = href
        recorded['detail'] = parsed_detail

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _fake_upsert)

    ok, failed = bm._apply_metadata_result(result, dry_run=False)

    assert (ok, failed) == (1, 0)
    assert recorded['href'] == '/v/abc'
    assert recorded['detail'] is detail


def test_apply_metadata_result_dry_run_skips_writes(monkeypatch):
    result = _make_metadata_result()

    def _boom(*_args, **_kwargs):
        raise AssertionError('MetadataRepo.upsert must not run in dry-run mode')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)

    ok, failed = bm._apply_metadata_result(result, dry_run=True)

    assert (ok, failed) == (1, 0)


def test_apply_metadata_result_failed_result_counts_as_failed(monkeypatch):
    result = _make_metadata_result(success=False, detail=None, error='boom')

    def _boom(*_args, **_kwargs):
        raise AssertionError('MetadataRepo.upsert must not run for failures')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)

    ok, failed = bm._apply_metadata_result(result, dry_run=False)

    assert (ok, failed) == (0, 1)


def test_apply_metadata_result_per_worker_cap_is_not_failure(monkeypatch):
    from javdb.spider.fetch.fetch_engine import PER_WORKER_TASK_CAP_ERROR

    result = _make_metadata_result(
        success=False,
        detail=None,
        error=PER_WORKER_TASK_CAP_ERROR,
        worker_name='engine',
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError('MetadataRepo.upsert must not run for cap flushes')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)

    ok, failed = bm._apply_metadata_result(result, dry_run=False)

    assert (ok, failed) == (0, 0)


def test_apply_metadata_result_write_failure_counts_as_failed(monkeypatch):
    result = _make_metadata_result()

    def _boom(*_args, **_kwargs):
        raise RuntimeError('write failed')

    monkeypatch.setattr(bm.MetadataRepo, 'upsert', _boom)

    ok, failed = bm._apply_metadata_result(result, dry_run=False)

    assert (ok, failed) == (0, 1)
