"""The indexer fetch helper must reuse proxy/curl_cffi but skip javdb guards."""

from javdb.integrations.indexer import fetch as indexer_fetch


class _FakeHandler:
    def __init__(self):
        self.calls = []

    def get_page(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return "<html>ok</html>"


def test_fetch_disables_cf_bypass_and_uses_indexer_module(monkeypatch):
    fake = _FakeHandler()
    monkeypatch.setattr(indexer_fetch, "_handler", lambda config: fake)
    html = indexer_fetch.fetch_source_html(
        "https://www.javbus.com/ABC-001",
        {"PROXY_POOL": []},
        use_proxy=True,
    )
    assert html == "<html>ok</html>"
    url, kwargs = fake.calls[0]
    assert url == "https://www.javbus.com/ABC-001"
    assert kwargs["use_cf_bypass"] is False
    assert kwargs["module_name"] == "indexer"
    assert kwargs["validate_html"] is False


def test_fetch_passes_timeout_to_request_handler(monkeypatch):
    fake = _FakeHandler()
    monkeypatch.setattr(indexer_fetch, "_handler", lambda config: fake)

    indexer_fetch.fetch_source_html(
        "https://www.javbus.com/ABC-001",
        {"PROXY_POOL": []},
        use_proxy=False,
        timeout=7.5,
    )

    _url, kwargs = fake.calls[0]
    assert kwargs["timeout"] == 7.5


def test_fetch_returns_none_on_empty(monkeypatch):
    class _Empty(_FakeHandler):
        def get_page(self, url, **kwargs):
            return None

    monkeypatch.setattr(indexer_fetch, "_handler", lambda config: _Empty())
    assert indexer_fetch.fetch_source_html("https://x", {}, use_proxy=False) is None


def test_proxy_pool_runtime_errors_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("rust proxy pool unavailable")

    monkeypatch.setattr(indexer_fetch, "create_proxy_pool_from_config", _boom)

    try:
        indexer_fetch._proxy_pool({"PROXY_POOL": [{"name": "p1"}]})
    except RuntimeError as exc:
        assert "rust proxy pool unavailable" in str(exc)
    else:
        raise AssertionError("RuntimeError should not be masked")


def test_proxy_pool_malformed_config_degrades_to_none(monkeypatch):
    def _bad_config(*args, **kwargs):
        raise ValueError("bad config")

    monkeypatch.setattr(indexer_fetch, "create_proxy_pool_from_config", _bad_config)
    assert indexer_fetch._proxy_pool({"PROXY_POOL": [{"name": "p1"}]}) is None


def test_handler_adds_indexer_to_existing_proxy_modules(monkeypatch):
    captured = {}

    def _fake_handler_factory(**kwargs):
        captured.update(kwargs)
        return _FakeHandler()

    monkeypatch.setattr(indexer_fetch, "create_request_handler_from_config", _fake_handler_factory)
    indexer_fetch._handler({"PROXY_MODULES": ["spider"], "PROXY_POOL": []})
    assert captured["proxy_modules"] == ["spider", "indexer"]
