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
    assert calls == [
        (
            "42",
            {
                "total_torrents": 3,
                "duplicate_count": 0,
                "attempted": 0,
                "successfully_added": 2,
                "failed_count": 1,
                "hacked_sub": 0,
                "hacked_nosub": 0,
                "subtitle_count": 0,
                "no_subtitle_count": 0,
                "success_rate": 0.0,
            },
        )
    ]


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
    assert calls == [
        (
            "99",
            {
                "threshold_days": 3,
                "total_torrents": 8,
                "filtered_old": 0,
                "successful_count": 5,
                "failed_count": 0,
                "uploaded_count": 0,
                "delete_failed_count": 0,
            },
        )
    ]


def test_save_pikpak_stats_preserves_zero_threshold_days(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)
    monkeypatch.setattr(stats_sink, "_current_backend", lambda: "sqlite")
    monkeypatch.setattr(
        stats_sink,
        "_db_save_pikpak_stats",
        lambda session_id, payload: calls.append((session_id, payload)),
    )

    stats_sink.save_pikpak_stats(
        "7",
        PikPakStats(threshold_days=0, total_torrents=4, successful_count=4, uploaded_count=0),
    )

    payload = calls[0][1]
    assert payload["threshold_days"] == 0
    assert payload["uploaded_count"] == 0


def test_save_uploader_stats_reports_storage_error(monkeypatch):
    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)

    def _boom(session_id, payload):
        raise RuntimeError("db down")

    monkeypatch.setattr(stats_sink, "_db_save_uploader_stats", _boom)

    result = stats_sink.save_uploader_stats("42", UploaderStats(total_torrents=1))

    assert result == StatsSinkResult(saved=False, backend=None, error="db down")


def test_save_uploader_stats_skips_when_sqlite_disabled(monkeypatch):
    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: False)

    result = stats_sink.save_uploader_stats("42", UploaderStats(total_torrents=1))

    assert result == StatsSinkResult(saved=False, backend=None, error=None)
