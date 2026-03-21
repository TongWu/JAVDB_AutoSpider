"""Tests for utils.spider_gateway — SDK, parse dispatch and API endpoint."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.spider_gateway import (
    SpiderGateway,
    GatewayResult,
    create_gateway,
    _PARSER_MAP,
)
from utils.rust_adapters.parser_adapter import result_to_dict


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
        from utils.rust_adapters import parser_adapter
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
