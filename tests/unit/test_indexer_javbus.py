"""JAVBUS plugin parse + is_configured honesty (ADR-054 WS3)."""

import pathlib
from urllib.parse import unquote
from urllib.parse import urlparse

import pytest

from javdb.integrations.indexer.javbus import plugin as javbus_plugin
from javdb.integrations.indexer.javbus.plugin import JavbusIndexerPlugin, parse

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures/indexer/javbus_ABC-001.html"
).read_text(encoding="utf-8")


def test_parse_extracts_magnets_with_infohash():
    magnets = parse(_FIXTURE)
    assert magnets, "fixture should yield at least one magnet"
    first = magnets[0]
    assert first.magnet_uri.startswith("magnet:")
    assert first.source == "javbus"
    assert first.info_hash is None or len(first.info_hash) == 40
    assert first.name == "ABC-001 HD Subtitle"
    assert first.size == "4.2 GB"
    assert first.tags == ["HD", "Subtitles"]


def test_parse_non_table_anchor_does_not_read_parent_cells_or_tags():
    html = """
    <html><body>
      <div>
        <span class="label">Wrong Tag</span>
        <td>Wrong Name</td><td>99 GB</td>
        <a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">Loose Anchor</a>
      </div>
    </body></html>
    """

    magnets = parse(html)

    assert len(magnets) == 1
    assert magnets[0].name == "Loose Anchor"
    assert magnets[0].size == ""
    assert magnets[0].tags == []


def test_is_configured_true_with_default_base():
    assert JavbusIndexerPlugin().is_configured() is True


@pytest.mark.parametrize(
    "video_code",
    [
        "https://example.com/a",
        "//example.com/a",
    ],
)
def test_search_keeps_url_like_video_code_on_configured_host(monkeypatch, video_code):
    calls = []

    monkeypatch.setattr(
        javbus_plugin,
        "cfg",
        lambda name, default: "https://javbus.example.test/base",
    )
    monkeypatch.setattr(javbus_plugin, "_runtime_config", lambda: {})

    def fake_fetch(url, config, use_proxy):
        calls.append(url)
        return _FIXTURE

    monkeypatch.setattr(javbus_plugin, "fetch_source_html", fake_fetch)

    result = JavbusIndexerPlugin().search(video_code)

    assert result.ok is True
    assert calls
    fetched_url = urlparse(calls[0])
    assert fetched_url.hostname == "javbus.example.test"


def test_runtime_config_fails_closed_on_load_error(monkeypatch):
    # issue #226 + #228: a config-load error in the registered provider must NOT
    # degrade to an empty (proxy-less) config that would scrape direct and leak
    # the operator IP. search() must raise (the dispatcher then marks the source
    # failed) and never fetch direct.
    from javdb.infra import runtime_config

    def boom():
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(runtime_config, "_provider", boom)
    fetched = []
    monkeypatch.setattr(
        javbus_plugin,
        "fetch_source_html",
        lambda url, config, use_proxy: fetched.append(url),
    )

    with pytest.raises(RuntimeError):
        JavbusIndexerPlugin().search("ABC-001")
    assert fetched == []


def test_search_url_encodes_special_video_code(monkeypatch):
    calls = []

    monkeypatch.setattr(
        javbus_plugin,
        "cfg",
        lambda name, default: "https://javbus.example.test/base",
    )
    monkeypatch.setattr(javbus_plugin, "_runtime_config", lambda: {})

    def fake_fetch(url, config, use_proxy):
        calls.append(url)
        return _FIXTURE

    monkeypatch.setattr(javbus_plugin, "fetch_source_html", fake_fetch)

    result = JavbusIndexerPlugin().search("ABC 001/中字")

    assert result.ok is True
    assert calls == ["https://javbus.example.test/base/ABC%20001%2F%E4%B8%AD%E5%AD%97"]
    assert unquote(urlparse(calls[0]).path).endswith("/ABC 001/中字")
