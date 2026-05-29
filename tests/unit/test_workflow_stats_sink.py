from __future__ import annotations

from javdb.workflow import stats_sink
from javdb.workflow.stats_sink import PikPakStats, StatsSinkResult, UploaderStats


def test_save_uploader_stats_skips_without_session_id():
    result = stats_sink.save_uploader_stats(None, UploaderStats(total_torrents=1))

    assert result == StatsSinkResult(saved=False, backend=None, error=None)


def test_save_uploader_stats_calls_storage(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)
    monkeypatch.setattr(stats_sink, "_current_backend", lambda: "sqlite")
    monkeypatch.setattr(
        stats_sink,
        "_db_save_uploader_stats",
        lambda session_id, payload: calls.append((session_id, payload)),
    )

    result = stats_sink.save_uploader_stats(
        "42",
        UploaderStats(total_torrents=3, successfully_added=2, failed_count=1),
    )

    assert result == StatsSinkResult(saved=True, backend="sqlite", error=None)
    assert calls == [("42", {"total_torrents": 3, "successfully_added": 2, "failed_count": 1})]


def test_save_pikpak_stats_calls_storage(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)
    monkeypatch.setattr(stats_sink, "_current_backend", lambda: "sqlite")
    monkeypatch.setattr(
        stats_sink,
        "_db_save_pikpak_stats",
        lambda session_id, payload: calls.append((session_id, payload)),
    )

    result = stats_sink.save_pikpak_stats(
        "99",
        PikPakStats(threshold_days=3, total_torrents=8, successful_count=5),
    )

    assert result == StatsSinkResult(saved=True, backend="sqlite", error=None)
    assert calls == [("99", {"threshold_days": 3, "total_torrents": 8, "successful_count": 5})]
