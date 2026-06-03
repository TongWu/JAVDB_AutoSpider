"""ADR-047 D1c: /summary counts TorrentHistory (not ReportTorrents) and computes
avg_duration from CommittedAt, matching the TS backend. DB-free: spy on the query
helper and assert the SQL the handler issues."""
import apps.api.routers.stats as stats


def test_summary_counts_torrent_history_and_computes_avg_duration(monkeypatch):
    calls = []
    monkeypatch.setattr(stats, "_safe_query_one", lambda db, sql, *a, **k: (calls.append((db, sql)), 0)[1])
    monkeypatch.setattr(stats, "_count_proxy_bans_in_logs", lambda *_: 0)

    stats.stats_summary(_user={"sub": "test"})

    assert (stats.HISTORY_DB_PATH, "SELECT COUNT(*) FROM TorrentHistory") in calls
    assert all("ReportTorrents" not in sql for _, sql in calls), "must not count ReportTorrents"
    assert any("CommittedAt" in sql for _, sql in calls), "avg_duration must query CommittedAt"
