"""ADR-047 D1c: /summary counts TorrentHistory (not ReportTorrents) and computes
avg_duration from CommittedAt, matching the TS backend. DB-free: spy on the query
helper and assert the SQL the handler issues."""
import re

import apps.api.routers.stats as stats


def test_summary_counts_torrent_history_and_computes_avg_duration(monkeypatch):
    calls = []
    monkeypatch.setattr(stats, "_safe_query_one", lambda db, sql, *a, **k: (calls.append((db, sql)), 0)[1])
    monkeypatch.setattr(stats, "_count_proxy_bans_in_logs", lambda *_: 0)

    stats.stats_summary(_user={"sub": "test"})

    history_count_queries = [
        sql.lower()
        for db, sql in calls
        if db == stats.HISTORY_DB_PATH and "torrenthistory" in sql.lower()
    ]
    sql_norms = [
        re.sub(r"\s+", " ", sql).strip().lower()
        for _, sql in calls
    ]
    sql_eq_norms = [
        re.sub(r"\s*=\s*", "=", sql_norm)
        for sql_norm in sql_norms
    ]
    assert any("select count" in sql for sql in history_count_queries)
    assert all("reporttorrents" not in sql for sql in sql_norms), "must not count ReportTorrents"
    assert any("committedat" in sql for sql in sql_norms), "avg_duration must query CommittedAt"
    assert any("isdeleted=1" in sql for sql in sql_eq_norms), "dedup must filter IsDeleted=1"
