"""Tests for utils.spider_gateway — SDK, parse dispatch and API endpoint."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.spider_gateway import (
    SpiderGateway,
    GatewayResult,
    CrawlResult,
    create_gateway,
    _PARSER_MAP,
    _build_page_url,
)
from utils.bridges.rust_adapters.parser_adapter import result_to_dict


# ---------------------------------------------------------------------------
# Fixtures — sample HTML pages for each type
# ---------------------------------------------------------------------------

@pytest.fixture
def index_html():
    return '''
    <html><head><title>JavDB</title></head><body>
    <div class="movie-list h cols-4 vcols-8">
        <div class="item"><a class="box" href="/v/ABC-123">
            <div class="video-title"><strong>ABC-123</strong></div>
            <div class="tags has-addons"><span class="tag">含中字磁鏈</span></div>
            <div class="score"><span class="value">4.47分, 由595人評價</span></div>
        </a></div>
    </div></body></html>
    '''


@pytest.fixture
def detail_html():
    return '''
    <html><head><title>ABC-123 Detail</title></head><body>
    <!-- saved from url=(0026)https://javdb.com/v/1AWMKA -->
    <div class="video-meta-panel">
        <div class="panel-block"><strong>演員:</strong>
            <span class="value"><a href="/actors/xyz">Test Actor</a></span>
        </div>
    </div>
    <div id="magnets-content">
        <div class="item columns is-desktop">
            <div class="magnet-name">
                <a href="magnet:?xt=urn:btih:abc123sub">
                    <span class="name">ABC-123-sub.torrent</span>
                    <span class="meta">4.94GB, 1個文件</span>
                    <div class="tags"><span class="tag">字幕</span></div>
                </a>
            </div>
            <span class="time">2024-01-15</span>
        </div>
    </div></body></html>
    '''


@pytest.fixture
def tag_html():
    return '''
    <html><head><title>Tags - JavDB</title></head><body>
    <!-- saved from url=(0040)https://javdb.com/tags?c6=1 -->
    <nav class="section-container tags-show">
        <div class="tags-filter">
            <div class="tag-category" data-category="c1">
                <span class="category-title">類別</span>
                <div class="tag-values">
                    <a class="tag-value" data-value="1" href="/tags?c1=1">Drama</a>
                </div>
            </div>
        </div>
    </nav>
    <div class="movie-list h cols-4 vcols-8">
        <div class="item"><a class="box" href="/v/TAG-001">
            <div class="video-title"><strong>TAG-001</strong></div>
            <div class="tags has-addons"><span class="tag">含磁鏈</span></div>
            <div class="score"><span class="value">4.0分, 由10人評價</span></div>
        </a></div>
    </div></body></html>
    '''


@pytest.fixture
def category_html():
    return '''
    <html><head><title>Category - JavDB</title></head><body>
    <!-- saved from url=(0040)https://javdb.com/makers/6M?f=download -->
    <div class="movie-list h cols-4 vcols-8">
        <div class="item"><a class="box" href="/v/CAT-001">
            <div class="video-title"><strong>CAT-001</strong></div>
            <div class="tags has-addons"><span class="tag">含磁鏈</span></div>
            <div class="score"><span class="value">4.5分, 由100人評價</span></div>
        </a></div>
    </div></body></html>
    '''


@pytest.fixture
def top_html():
    return '''
    <html><head><title>Top 250 - JavDB</title></head><body>
    <!-- saved from url=(0038)https://javdb.com/rankings/top?t=y2025 -->
    <div class="movie-list h cols-4 vcols-8">
        <div class="item"><a class="box" href="/v/TOP-001">
            <div class="video-title"><strong>TOP-001</strong></div>
            <div class="tags has-addons"><span class="tag">含磁鏈</span></div>
            <div class="score"><span class="value">4.8分, 由300人評價</span></div>
        </a></div>
    </div></body></html>
    '''


@pytest.fixture
def login_html():
    return '<html><head><title>登入 - JavDB</title></head><body></body></html>'


@pytest.fixture
def empty_html():
    return '<html><head><title>JavDB</title></head><body></body></html>'


# ---------------------------------------------------------------------------
# Helper to build a gateway with a mocked handler
# ---------------------------------------------------------------------------

def _make_gateway(return_html=None):
    mock_handler = MagicMock()
    mock_handler.get_page.return_value = return_html
    return SpiderGateway(mock_handler, use_proxy=True, use_cf_bypass=True)


# ---------------------------------------------------------------------------
# SDK: parse_html — auto-detect + dispatch (no network)
# ---------------------------------------------------------------------------

class TestParseHtml:
    def test_index_page(self, index_html):
        gw = _make_gateway()
        r = gw.parse_html(index_html, page_num=1)
        assert r.ok is True
        assert r.page_type == 'index'
        assert 'movies' in r.result

    def test_detail_page(self, detail_html):
        gw = _make_gateway()
        r = gw.parse_html(detail_html, page_num=1)
        assert r.ok is True
        assert r.page_type == 'detail'
        assert 'magnets' in r.result

    def test_tag_page(self, tag_html):
        gw = _make_gateway()
        r = gw.parse_html(tag_html, page_num=1)
        assert r.ok is True
        assert r.page_type == 'tags'

    def test_category_page(self, category_html):
        gw = _make_gateway()
        r = gw.parse_html(category_html, page_num=1)
        assert r.ok is True
        assert r.page_type == 'makers'

    def test_top_page(self, top_html):
        gw = _make_gateway()
        r = gw.parse_html(top_html, page_num=1)
        assert r.ok is True
        assert r.page_type == 'top250'

    def test_unknown_page(self, empty_html):
        gw = _make_gateway()
        r = gw.parse_html(empty_html)
        assert r.ok is False
        assert 'Unsupported' in (r.error or '')


# ---------------------------------------------------------------------------
# SDK: fetch_and_parse — full round-trip (mocked fetch)
# ---------------------------------------------------------------------------

class TestFetchAndParse:
    def test_success(self, index_html):
        gw = _make_gateway(return_html=index_html)
        r = gw.fetch_and_parse('https://javdb.com/', page_num=1)
        assert r.ok is True
        assert r.page_type == 'index'
        assert r.url == 'https://javdb.com/'
        assert r.html_len > 0

    def test_fetch_failure(self):
        gw = _make_gateway(return_html=None)
        r = gw.fetch_and_parse('https://javdb.com/v/fail')
        assert r.ok is False
        assert 'Failed to fetch' in r.error

    def test_detail_roundtrip(self, detail_html):
        gw = _make_gateway(return_html=detail_html)
        r = gw.fetch_and_parse('https://javdb.com/v/1AWMKA')
        assert r.ok is True
        assert r.page_type == 'detail'


# ---------------------------------------------------------------------------
# GatewayResult.to_dict
# ---------------------------------------------------------------------------

class TestGatewayResult:
    def test_to_dict_keys(self):
        gr = GatewayResult(ok=True, page_type='index', url='u', html_len=100)
        d = gr.to_dict()
        assert set(d.keys()) == {
            'ok', 'page_type', 'url', 'html_len',
            'used_proxy', 'used_cf_bypass', 'result', 'error',
        }

    def test_error_case(self):
        gr = GatewayResult(ok=False, error='boom')
        assert gr.to_dict()['error'] == 'boom'


# ---------------------------------------------------------------------------
# Parser map coverage
# ---------------------------------------------------------------------------

class TestParserMap:
    def test_all_expected_types_covered(self):
        expected = {
            'index', 'search', 'category', 'makers', 'publishers',
            'series', 'directors', 'actors', 'top250', 'tags', 'detail',
        }
        assert expected == set(_PARSER_MAP.keys())


# ---------------------------------------------------------------------------
# Rust-fallback: gateway still works when rust_core not installed
# ---------------------------------------------------------------------------

class TestRustFallback:
    def test_parse_html_python_fallback(self, index_html, monkeypatch):
        from utils.bridges.rust_adapters import parser_adapter
        monkeypatch.setattr(parser_adapter, 'RUST_PARSER_EXTRAS_AVAILABLE', False)
        gw = _make_gateway()
        r = gw.parse_html(index_html)
        assert r.ok is True
        assert r.page_type == 'index'


# ---------------------------------------------------------------------------
# API endpoint: /api/parse/url (unit-test via TestClient if fastapi available)
# ---------------------------------------------------------------------------

class TestApiParseUrl:
    def test_endpoint_exists(self):
        from api.server import app
        routes = [r.path for r in app.routes]
        assert '/api/parse/url' in routes

    def test_endpoint_schema(self):
        from api.server import UrlPayload
        p = UrlPayload(url='https://javdb.com/')
        assert p.page_num == 1
        assert p.use_proxy is True
        assert p.use_cf_bypass is True
        assert p.use_cookie is False

    def test_parse_url_respects_payload_params(self):
        """Verify that /api/parse/url creates a gateway with request params
        instead of using a hardcoded singleton."""
        from api.server import app, _jwt_encode
        from fastapi.testclient import TestClient

        token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
        csrf = "test-csrf-value"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": csrf,
        }

        with patch('api.server.create_gateway') as mock_create:
            mock_gw = MagicMock()
            mock_gw.fetch_and_parse.return_value = GatewayResult(
                ok=True, page_type='index', url='u', html_len=10, result={},
            )
            mock_create.return_value = mock_gw

            client = TestClient(app, cookies={"csrf_token": csrf})
            client.post('/api/parse/url', json={
                'url': 'https://javdb.com/',
                'use_proxy': False,
                'use_cf_bypass': False,
                'use_cookie': True,
            }, headers=headers)

            mock_create.assert_called_once_with(
                use_proxy=False,
                use_cf_bypass=False,
                use_cookie=True,
            )

    def test_compatibility_exports_still_available(self):
        from api.server import (
            CrawlIndexPayload,
            SpiderJobPayload,
            UrlPayload,
            _jwt_encode,
            _payload_to_cli_args,
            create_gateway,
        )

        assert UrlPayload(url='https://javdb.com/').page_num == 1
        assert CrawlIndexPayload(url='https://javdb.com/').start_page == 1
        assert SpiderJobPayload().phase == 'all'
        assert callable(_jwt_encode)
        assert callable(_payload_to_cli_args)
        assert callable(create_gateway)


class TestApiRouteRegistry:
    def test_expected_paths_present(self):
        from api.server import app

        expected = {
            '/api/health',
            '/api/auth/login',
            '/api/auth/refresh',
            '/api/auth/logout',
            '/api/config',
            '/api/config/meta',
            '/api/tasks/daily',
            '/api/tasks/adhoc',
            '/api/tasks',
            '/api/tasks/stats',
            '/api/tasks/{job_id}',
            '/api/tasks/{job_id}/stream',
            '/api/explore/sync-cookie',
            '/api/explore/proxy-page',
            '/api/explore/resolve',
            '/api/explore/download-magnet',
            '/api/explore/one-click',
            '/api/explore/index-status',
            '/api/health-check',
            '/api/login/refresh',
            '/api/parse/index',
            '/api/parse/detail',
            '/api/parse/category',
            '/api/parse/top',
            '/api/parse/tags',
            '/api/detect-page-type',
            '/api/parse/url',
            '/api/crawl/index',
            '/api/jobs/spider',
            '/api/jobs/{job_id}/status',
        }
        routes = {route.path for route in app.routes}
        assert expected.issubset(routes)

    def test_no_duplicate_path_method_pairs(self):
        from api.server import app
        from fastapi.routing import APIRoute

        seen = {}
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                key = (route.path, method)
                assert key not in seen, f'duplicate route registered for {key}'
                seen[key] = route.endpoint.__name__


# ---------------------------------------------------------------------------
# _build_page_url helper
# ---------------------------------------------------------------------------

class TestBuildPageUrl:
    def test_page_1_returns_original(self):
        assert _build_page_url('https://javdb.com/', 1) == 'https://javdb.com/'

    def test_page_n_appends_query(self):
        assert _build_page_url('https://javdb.com/', 3) == 'https://javdb.com/?page=3'

    def test_existing_query_uses_ampersand(self):
        url = 'https://javdb.com/tags?c6=1'
        assert _build_page_url(url, 2) == 'https://javdb.com/tags?c6=1&page=2'


# ---------------------------------------------------------------------------
# SDK: crawl_pages — multi-page crawl (mocked fetch)
# ---------------------------------------------------------------------------

class TestCrawlPages:
    def test_crawl_fixed_range(self, index_html):
        gw = _make_gateway(return_html=index_html)
        cr = gw.crawl_pages(
            'https://javdb.com/', start_page=1, end_page=3, page_delay=0,
        )
        assert cr.ok is True
        assert cr.total_pages == 3
        assert len(cr.pages) == 3
        assert all(p.ok for p in cr.pages)

    def test_crawl_all_stops_on_empty(self, empty_html):
        gw = _make_gateway(return_html=empty_html)
        cr = gw.crawl_pages(
            'https://javdb.com/', crawl_all=True,
            max_consecutive_empty=2, page_delay=0,
        )
        assert cr.ok is True
        assert cr.total_pages == 2

    def test_crawl_all_mixed_pages(self, index_html, empty_html):
        mock_handler = MagicMock()
        responses = [index_html, index_html, empty_html, empty_html]
        mock_handler.get_page.side_effect = responses
        gw = SpiderGateway(mock_handler, use_proxy=True, use_cf_bypass=True)

        cr = gw.crawl_pages(
            'https://javdb.com/', crawl_all=True,
            max_consecutive_empty=2, page_delay=0,
        )
        assert cr.total_pages == 4
        assert cr.pages[0].ok is True
        assert cr.pages[1].ok is True

    def test_crawl_result_to_dict(self):
        cr = CrawlResult(ok=True, total_pages=1, pages=[
            GatewayResult(ok=True, page_type='index', url='u', html_len=10),
        ])
        d = cr.to_dict()
        assert d['ok'] is True
        assert d['total_pages'] == 1
        assert len(d['pages']) == 1
        assert d['pages'][0]['page_type'] == 'index'


# ---------------------------------------------------------------------------
# API endpoint: /api/crawl/index
# ---------------------------------------------------------------------------

class TestApiCrawlIndex:
    def test_endpoint_exists(self):
        from api.server import app
        routes = [r.path for r in app.routes]
        assert '/api/crawl/index' in routes

    def test_crawl_index_schema(self):
        from api.server import CrawlIndexPayload
        p = CrawlIndexPayload(url='https://javdb.com/')
        assert p.start_page == 1
        assert p.end_page is None
        assert p.crawl_all is False
        assert p.use_proxy is True


# ---------------------------------------------------------------------------
# API endpoint: /api/jobs/spider
# ---------------------------------------------------------------------------

class TestApiSpiderJob:
    def test_endpoints_exist(self):
        from api.server import app
        routes = [r.path for r in app.routes]
        assert '/api/jobs/spider' in routes
        assert '/api/jobs/{job_id}/status' in routes

    def test_spider_job_payload_defaults(self):
        from api.server import SpiderJobPayload
        p = SpiderJobPayload()
        assert p.url is None
        assert p.start_page == 1
        assert p.phase == 'all'
        assert p.use_proxy is False
        assert p.dry_run is False
        assert p.disable_all_filters is False

    def test_spider_job_payload_custom(self):
        from api.server import SpiderJobPayload
        p = SpiderJobPayload(
            url='https://javdb.com/tags?c6=1',
            start_page=2,
            end_page=5,
            phase='1',
            ignore_history=True,
            enable_redownload=True,
            redownload_threshold=0.50,
            dry_run=True,
        )
        assert p.url == 'https://javdb.com/tags?c6=1'
        assert p.phase == '1'
        assert p.redownload_threshold == 0.50

    def test_payload_to_cli_args_minimal(self):
        from api.server import SpiderJobPayload, _payload_to_cli_args
        p = SpiderJobPayload()
        args = _payload_to_cli_args(p)
        assert '--use-proxy' not in args
        assert '--url' not in args
        assert '--dry-run' not in args

    def test_payload_to_cli_args_full(self):
        from api.server import SpiderJobPayload, _payload_to_cli_args
        p = SpiderJobPayload(
            url='https://javdb.com/tags?c6=1',
            start_page=3,
            end_page=10,
            phase='1',
            ignore_history=True,
            use_history=True,
            ignore_release_date=True,
            no_rclone_filter=True,
            disable_all_filters=True,
            enable_dedup=True,
            enable_redownload=True,
            redownload_threshold=0.40,
            dry_run=True,
            max_movies_phase1=5,
            max_movies_phase2=10,
        )
        args = _payload_to_cli_args(p)
        assert args[:2] == ['--url', 'https://javdb.com/tags?c6=1']
        assert '--start-page' in args
        assert args[args.index('--start-page') + 1] == '3'
        assert '--end-page' in args
        assert args[args.index('--end-page') + 1] == '10'
        assert '--phase' in args
        assert args[args.index('--phase') + 1] == '1'
        assert '--ignore-history' in args
        assert '--use-history' in args
        assert '--ignore-release-date' in args
        assert '--no-rclone-filter' in args
        assert '--disable-all-filters' in args
        assert '--enable-dedup' in args
        assert '--enable-redownload' in args
        assert '--redownload-threshold' in args
        assert args[args.index('--redownload-threshold') + 1] == '0.4'
        assert '--dry-run' in args
        assert '--max-movies-phase1' in args
        assert args[args.index('--max-movies-phase1') + 1] == '5'
        assert '--max-movies-phase2' in args
        assert args[args.index('--max-movies-phase2') + 1] == '10'

    def test_payload_to_cli_args_crawl_all(self):
        from api.server import SpiderJobPayload, _payload_to_cli_args
        p = SpiderJobPayload(crawl_all=True, use_proxy=False)
        args = _payload_to_cli_args(p)
        assert '--all' in args
        assert '--use-proxy' not in args

    def test_job_not_found_returns_404(self):
        from api.server import app, _jwt_encode
        from fastapi.testclient import TestClient

        token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
        headers = {"Authorization": f"Bearer {token}"}
        client = TestClient(app)
        resp = client.get('/api/jobs/nonexistent/status', headers=headers)
        assert resp.status_code == 404
