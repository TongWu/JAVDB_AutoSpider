"""Dump the dual-backend query Contract Golden to docs/api/contract/ (ADR-018)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.cli.ops.query_contract_cases import (  # noqa: E402
    CONSUMPTION_RECENT_QUERY_CASES,
    CONSUMPTION_SUMMARY_QUERY_CASES,
    CONSUMPTION_SUMMARY_UNRESOLVED_COUNT_QUERY_CASES,
    CONSUMPTION_TREND_QUERY_CASES,
    CONSUMPTION_UNRESOLVED_QUERY_CASES,
    LIBRARY_RECENT_QUERY_CASES,
    LIBRARY_SUMMARY_QUERY_CASES,
    LIBRARY_TREND_QUERY_CASES,
    MOVIE_COUNT_CASES,
    MOVIE_FILTER_CASES,
    OWNERSHIP_RECENT_QUERY_CASES,
    OWNERSHIP_SUMMARY_BY_SOURCE_QUERY_CASES,
    OWNERSHIP_SUMMARY_DISTINCT_QUERY_CASES,
    SESSION_QUERY_CASES,
    STATS_TREND_QUERY_CASES,
    TORRENT_COUNT_CASES,
    TORRENT_FILTER_CASES,
    normalize_sql,
)
from apps.api.routers.library_query_builders import (  # noqa: E402
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)
from apps.api.routers.library_ownership_query_builders import (  # noqa: E402
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)
from apps.api.routers.library_consumption_query_builders import (  # noqa: E402
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)
from apps.api.routers.stats_query_builders import build_stats_trend_query  # noqa: E402
from javdb.storage.repos.history_repo import (  # noqa: E402
    _build_movie_filters,
    _build_torrent_filters,
    build_movie_count,
    build_torrent_count,
)
from javdb.storage.repos.sessions_repo import (  # noqa: E402
    _build_session_query,
    _encode_cursor,
)

OUT = REPO_ROOT / "docs" / "api" / "contract" / "query-builders.golden.json"


def _build_stats_trend_query_for_contract(*, metric: str, cutoff: str) -> tuple[str, list]:
    query = build_stats_trend_query(metric=metric, cutoff=cutoff)
    db_alias_by_metric = {
        "success_rate": "reports",
        "movies": "reports",
        "torrents": "reports",
        "history_growth": "history",
        "pikpak": "operations",
        "dedup": "operations",
    }
    return query.sql, [db_alias_by_metric[metric], *query.params]


_BUILDERS = {
    "movie_filters": _build_movie_filters,
    "movie_count": build_movie_count,
    "torrent_filters": _build_torrent_filters,
    "torrent_count": build_torrent_count,
    "session_query": _build_session_query,
    "stats_trend_query": _build_stats_trend_query_for_contract,
    "library_summary_query": build_acquisition_summary_query,
    "library_recent_query": build_acquisition_recent_query,
    "library_trend_query": build_acquisition_trend_query,
    "ownership_summary_by_source_query": build_ownership_summary_by_source_query,
    "ownership_summary_distinct_query": build_ownership_summary_distinct_query,
    "ownership_recent_query": build_ownership_recent_query,
    "consumption_summary_query": build_consumption_summary_query,
    "consumption_summary_unresolved_count_query": build_consumption_summary_unresolved_count_query,
    "consumption_recent_query": build_consumption_recent_query,
    "consumption_trend_query": build_consumption_trend_query,
    "consumption_unresolved_query": build_consumption_unresolved_query,
}


def _run_case(builder_id: str, kwargs: dict):
    kw = dict(kwargs)
    if kw.get("cursor") == "<ENCODED>":
        # Resolve the sentinel at runtime so the cursor scheme stays in the loop.
        kw["cursor"] = _encode_cursor("99999")
    sql, bindings = _BUILDERS[builder_id](**kw)
    return normalize_sql(sql), list(bindings), kw


def main() -> int:
    cases = []
    for builder_id, name, kwargs in (
        *MOVIE_FILTER_CASES,
        *MOVIE_COUNT_CASES,
        *TORRENT_FILTER_CASES,
        *TORRENT_COUNT_CASES,
        *SESSION_QUERY_CASES,
        *STATS_TREND_QUERY_CASES,
        *LIBRARY_SUMMARY_QUERY_CASES,
        *LIBRARY_RECENT_QUERY_CASES,
        *LIBRARY_TREND_QUERY_CASES,
        *OWNERSHIP_SUMMARY_BY_SOURCE_QUERY_CASES,
        *OWNERSHIP_SUMMARY_DISTINCT_QUERY_CASES,
        *OWNERSHIP_RECENT_QUERY_CASES,
        *CONSUMPTION_SUMMARY_QUERY_CASES,
        *CONSUMPTION_SUMMARY_UNRESOLVED_COUNT_QUERY_CASES,
        *CONSUMPTION_RECENT_QUERY_CASES,
        *CONSUMPTION_TREND_QUERY_CASES,
        *CONSUMPTION_UNRESOLVED_QUERY_CASES,
    ):
        sql, bindings, resolved = _run_case(builder_id, kwargs)
        cases.append(
            {
                "builder": builder_id,
                "name": name,
                "params": resolved,
                "sql": sql,
                "bindings": bindings,
            }
        )
    version = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:16]
    doc = {
        "version": version,  # content hash (D6); rides the Phase-2 repository_dispatch payload
        "normalization": "collapse-whitespace-runs-to-single-space-and-trim",
        "cases": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
