# tests/unit/test_library_ownership_query_builders.py
"""TDD tests for library ownership query builders (ADR-034 FE-2)."""

from apps.api.routers.library_ownership_query_builders import (
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)


def test_summary_by_source_groups_by_source():
    sql, bindings = build_ownership_summary_by_source_query()
    assert bindings == []
    assert "FROM OwnershipLedger" in sql
    assert "GROUP BY source" in sql
    assert "COUNT(DISTINCT video_code)" in sql
    assert "COALESCE(SUM(size), 0)" in sql
    assert "present = 1" in sql


def test_summary_distinct_counts_unique_present_titles():
    sql, bindings = build_ownership_summary_distinct_query()
    assert bindings == []
    assert "COUNT(DISTINCT video_code)" in sql
    assert "FROM OwnershipLedger" in sql
    assert "WHERE present = 1" in sql


def test_recent_without_source_omits_where():
    sql, bindings = build_ownership_recent_query(source=None, limit=50, offset=0)
    assert "WHERE" not in sql
    # Pin the full deterministic tie-breaker (PK cols) for stable pagination
    assert "ORDER BY observed_at DESC, video_code, source, category" in sql
    assert bindings == [50, 0]


def test_recent_with_source_binds_source_first():
    sql, bindings = build_ownership_recent_query(source="qb", limit=20, offset=10)
    assert "WHERE source = ?" in sql
    assert bindings == ["qb", 20, 10]


def test_recent_selects_all_required_columns():
    sql, _ = build_ownership_recent_query(source=None, limit=50, offset=0)
    for col in ("video_code", "source", "category", "path", "size", "present", "observed_at"):
        assert col in sql
