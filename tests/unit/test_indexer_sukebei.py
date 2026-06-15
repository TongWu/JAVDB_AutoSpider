"""Sukebei plugin parse + is_configured honesty (ADR-054 WS3)."""

import pathlib
from urllib.parse import parse_qs, urlparse

from javdb.integrations.indexer.sukebei import plugin as sukebei_plugin
from javdb.integrations.indexer.sukebei.plugin import SukebeiIndexerPlugin, parse

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures/indexer/sukebei_ABC-001.html"
).read_text(encoding="utf-8")


def test_parse_extracts_magnets_with_infohash():
    magnets = parse(_FIXTURE)
    assert magnets, "fixture should yield at least one magnet"
    first = magnets[0]
    assert first.magnet_uri.startswith("magnet:")
    assert first.source == "sukebei"
    assert first.info_hash is None or len(first.info_hash) == 40
    assert first.size == "5.1 GiB"
    assert first.name == "ABC-001 HD Subtitle"


def test_is_configured_true_with_default_base():
    assert SukebeiIndexerPlugin().is_configured() is True


def test_search_uses_configured_base_url_and_encoded_query(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sukebei_plugin,
        "cfg",
        lambda name, default: "https://sukebei.example.test/custom",
    )
    monkeypatch.setattr(sukebei_plugin, "_runtime_config", lambda: {})

    def fake_fetch(url, config, use_proxy):
        calls.append(url)
        return _FIXTURE

    monkeypatch.setattr(sukebei_plugin, "fetch_source_html", fake_fetch)

    result = SukebeiIndexerPlugin().search("ABC 001/中字")

    assert result.ok is True
    parsed = urlparse(calls[0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "sukebei.example.test"
    assert parsed.path == "/custom/"
    assert parsed.query == "q=ABC%20001%2F%E4%B8%AD%E5%AD%97"
    assert parse_qs(parsed.query)["q"] == ["ABC 001/中字"]


def test_search_empty_response_reports_failure(monkeypatch):
    monkeypatch.setattr(
        sukebei_plugin,
        "cfg",
        lambda name, default: "https://sukebei.example.test",
    )
    monkeypatch.setattr(sukebei_plugin, "_runtime_config", lambda: {})
    monkeypatch.setattr(sukebei_plugin, "fetch_source_html", lambda *args, **kwargs: None)

    result = SukebeiIndexerPlugin().search("ABC-001")

    assert result.ok is False
    assert result.magnets == []
    assert result.detail == "empty response"
