# tests/unit/test_library_consumption_query_builders.py
"""TDD tests for library consumption query builders (ADR-034 FE-3)."""

from apps.api.routers.library_consumption_query_builders import (
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)


def test_summary_counts_from_consumption_signal():
    sql, bindings = build_consumption_summary_query()
    assert bindings == []
    assert "FROM ConsumptionSignal" in sql
    assert "COUNT(*) AS total_signals" in sql
    assert "COUNT(DISTINCT video_code) AS unique_titles" in sql
    assert "COUNT(DISTINCT instance) AS instance_count" in sql
    # avg_rating NOT coalesced — NULL propagation is intentional
    assert "AVG(rating) AS avg_rating" in sql
    assert "COALESCE" not in sql.split("AVG")[1].split("\n")[0]  # no COALESCE on the AVG line


def test_summary_unresolved_count_from_unresolved_table():
    sql, bindings = build_consumption_summary_unresolved_count_query()
    assert bindings == []
    assert "COUNT(*) AS unresolved_count" in sql
    assert "FROM UnresolvedMediaItem" in sql


def test_recent_without_filters_omits_where():
    sql, bindings = build_consumption_recent_query(instance=None, watched=None, limit=50, offset=0)
    assert "WHERE" not in sql
    # Pin the full deterministic tie-breaker (PK cols) for stable pagination
    assert "ORDER BY observed_at DESC, video_code, instance, library_id" in sql
    assert bindings == [50, 0]


def test_recent_with_instance_filter():
    sql, bindings = build_consumption_recent_query(instance="emby-home", watched=None, limit=50, offset=0)
    assert "WHERE instance = ?" in sql
    assert bindings[0] == "emby-home"
    assert bindings[-2:] == [50, 0]


def test_recent_with_watched_true():
    sql, bindings = build_consumption_recent_query(instance=None, watched=True, limit=50, offset=0)
    assert "WHERE watched = 1" in sql
    assert bindings == [50, 0]


def test_recent_with_watched_false():
    sql, bindings = build_consumption_recent_query(instance=None, watched=False, limit=50, offset=0)
    assert "WHERE watched = 0" in sql
    assert bindings == [50, 0]


def test_recent_with_instance_and_watched():
    sql, bindings = build_consumption_recent_query(instance="plex-main", watched=True, limit=20, offset=10)
    assert "WHERE" in sql
    assert "instance = ?" in sql
    assert "watched = 1" in sql
    assert bindings[0] == "plex-main"
    assert bindings[-2:] == [20, 10]


def test_trend_uses_substr_and_watched_at_not_null():
    sql, bindings = build_consumption_trend_query(cutoff="2026-01-01")
    assert "substr(watched_at, 1, 10)" in sql
    assert "watched_at IS NOT NULL" in sql
    assert "watched_at >= ?" in sql
    assert "GROUP BY" in sql
    assert bindings == ["2026-01-01"]


def test_trend_counts_watched_and_total():
    sql, _ = build_consumption_trend_query(cutoff="2026-01-01")
    assert "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched" in sql
    assert "COUNT(*) AS total_signals" in sql


def test_unresolved_without_instance_omits_where():
    sql, bindings = build_consumption_unresolved_query(instance=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "FROM UnresolvedMediaItem" in sql
    # Pin the full deterministic tie-breaker (PK cols) for stable pagination
    assert "ORDER BY observed_at DESC, instance, library_id, item_id" in sql
    assert bindings == [50, 0]


def test_unresolved_with_instance_filter():
    sql, bindings = build_consumption_unresolved_query(instance="emby-home", limit=25, offset=5)
    assert "WHERE instance = ?" in sql
    assert bindings == ["emby-home", 25, 5]
