import pytest

from tests.harness.fixture_http import FixtureHTTP, record_enabled


def test_record_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "1")
    assert record_enabled() is True
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "off")
    assert record_enabled() is False
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    assert record_enabled() is False


def test_record_on_miss_fetches_and_remembers(monkeypatch):
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "1")
    calls = []

    def fake_live(url, *a, **k):
        calls.append(url)
        return "<html>live</html>"

    http = FixtureHTTP({}, record_miss=True, live_fetch=fake_live)
    assert http.get_page("https://javdb.com/v/NEW") == "<html>live</html>"
    assert calls == ["https://javdb.com/v/NEW"]
    assert http.recorded == {"https://javdb.com/v/NEW": "<html>live</html>"}
    # A recorded page is now a replay hit, not a tracked miss.
    assert http.misses == []
    assert http.get_page("https://javdb.com/v/NEW") == "<html>live</html>"
    assert calls == ["https://javdb.com/v/NEW"]  # second read served from cassette


def test_record_disarmed_without_env_stays_replay_only(monkeypatch):
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    http = FixtureHTTP({}, record_miss=True, live_fetch=lambda *a, **k: "x")
    assert http.get_page("https://javdb.com/v/NEW") is None
    assert "https://javdb.com/v/NEW" in http.misses
    assert http.recorded == {}


def test_record_on_miss_live_fetch_none_falls_back_to_tracked_miss(monkeypatch):
    # Armed (env on, record_miss, live_fetch), but the live fetch itself fails
    # (returns None) -> fall through to a tracked miss, never write `recorded`.
    monkeypatch.setenv("JAVDB_HARNESS_RECORD", "1")
    http = FixtureHTTP({}, record_miss=True, live_fetch=lambda *a, **k: None)
    assert http.get_page("https://javdb.com/v/NEW") is None
    assert http.recorded == {}
    assert http.misses == ["https://javdb.com/v/NEW"]


def test_record_pages_refuses_without_env(monkeypatch):
    monkeypatch.delenv("JAVDB_HARNESS_RECORD", raising=False)
    from tests.harness.recording import record_pages
    with pytest.raises(RuntimeError):
        record_pages(["https://javdb.com?page=1"])


@pytest.mark.skipif(not record_enabled(), reason="dev-only live network record")
def test_record_pages_live_smoke():
    from tests.harness.recording import record_pages
    pages = record_pages(["https://javdb.com?page=1"])
    assert pages  # at least the index recorded
