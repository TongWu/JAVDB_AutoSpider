"""Unit tests for library_query_builders (ADR-034 FE-1 parity contract)."""

from apps.api.routers.library_query_builders import (
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)


def test_summary_query_is_param_free_count():
    sql, bindings = build_acquisition_summary_query()
    assert bindings == []
    assert "FROM AcquisitionOutcome" in sql
    assert "COUNT(*) AS total" in sql
    for state in ("queued", "downloading", "completed", "stalled", "failed"):
        assert f"state='{state}'" in sql


def test_recent_query_without_state_omits_where():
    sql, bindings = build_acquisition_recent_query(state=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "ORDER BY queued_at DESC" in sql
    assert bindings == [50, 0]


def test_recent_query_with_state_binds_state_first():
    sql, bindings = build_acquisition_recent_query(state="completed", limit=20, offset=40)
    assert "WHERE state = ?" in sql
    assert bindings == ["completed", 20, 40]


def test_trend_query_uses_substr_day_and_terminal_states():
    sql, bindings = build_acquisition_trend_query(cutoff="2026-01-01")
    assert "substr(last_seen_at, 1, 10)" in sql
    assert "state IN ('completed','stalled','failed')" in sql
    assert "last_seen_at >= ?" in sql
    assert "GROUP BY d ORDER BY d" in sql
    assert bindings == ["2026-01-01"]
