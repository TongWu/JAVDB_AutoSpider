"""ADR-024 IMP-08 Task 4: gated assist evaluator (orchestration).

When TORRENT_QUALITY_POLICY_MODE='assist', re-scores each movie's
TorrentQualityEvidence rows (production + probe), ranks them within their
inferred category, and UPSERTs TorrentQualityEvaluation rows tagged
policy_mode='assist' with shadow_rank + would_replace_current_choice set.

This module NEVER touches the uploader, magnet selection, or download decision.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Optional

from javdb.quality.assist import rank_candidates
from javdb.quality.features import PROBE_SCHEMA_VERSION
from javdb.quality.models import EvaluationRecord
from javdb.quality.scoring import SCORING_VERSION, score_torrent

logger = logging.getLogger(__name__)


def _features_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Map promoted evidence columns + features_json.main_video_name into a
    features dict suitable for score_torrent."""
    features: dict[str, Any] = {
        "total_size_bytes": row.get("total_size_bytes"),
        "main_video_size_bytes": row.get("main_video_size_bytes"),
        "main_video_ratio": row.get("main_video_ratio"),
        "video_file_count": row.get("video_file_count"),
        "subtitle_file_count": row.get("subtitle_file_count"),
        "non_video_file_count": row.get("non_video_file_count"),
        "junk_size_bytes": row.get("junk_size_bytes"),
        "junk_size_ratio": row.get("junk_size_ratio"),
        "suspicious_file_count": row.get("suspicious_file_count"),
    }
    # Decode main_video_name from features_json if present, else fall back to a
    # top-level column (e.g. when the fake in the test supplies it directly).
    main_video_name = row.get("main_video_name")
    if main_video_name is None:
        features_json = row.get("features_json")
        if features_json:
            if isinstance(features_json, str):
                try:
                    features_json = json.loads(features_json)
                except (TypeError, ValueError):
                    features_json = {}
            main_video_name = (features_json or {}).get("main_video_name")
    features["main_video_name"] = main_video_name
    return features


def _context_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Build scoring context from evidence row metadata."""
    return {
        "javdb_category": row.get("javdb_category") or "",
        "magnet_name": row.get("magnet_name") or "",
        "javdb_tags": [],
    }


def evaluate_assist_for_movies(
    movie_hrefs: list[str],
    *,
    repo: Any,
    policy_mode: str,
    scoring_version: str = SCORING_VERSION,
) -> dict[str, int]:
    """Score + rank evidence for each movie and UPSERT assist evaluations.

    Returns a summary dict with keys: movies, candidates, would_replace.
    Is a no-op (returns zeroed summary) unless policy_mode == 'assist'.
    """
    zeroed: dict[str, int] = {"movies": 0, "candidates": 0, "would_replace": 0}
    if policy_mode != "assist":
        return zeroed

    total_candidates = 0
    total_would_replace = 0
    movies_processed = 0

    for movie_href in movie_hrefs:
        # Scope to the active probe schema so an older-schema evidence row for the
        # same info_hash can't leak stale features into the ranking.
        evidence_rows = repo.list_evidence_for_movie(
            movie_href, probe_schema_version=PROBE_SCHEMA_VERSION
        )
        if not evidence_rows:
            continue

        # Score each candidate
        scored_candidates: list[dict[str, Any]] = []
        for row in evidence_rows:
            features = _features_from_evidence(row)
            context = _context_from_evidence(row)
            scored = score_torrent(features, context)
            scored_candidates.append({
                "info_hash": row["info_hash"],
                "target_role": row["target_role"],
                "movie_href": row.get("movie_href", movie_href),
                "javdb_category": row.get("javdb_category"),
                "magnet_name": row.get("magnet_name"),
                "score": scored["score"],
                "decision": scored["decision"],
                "reasons": scored["reasons"],
                "inferred_category": scored["inferred_category"],
                "category_consistent": scored["category_consistent"],
                "subtitle_evidence": scored["subtitle_evidence"],
                "resolution_consistent": scored["resolution_consistent"],
            })

        # Group by inferred_category, rank within each group
        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in scored_candidates:
            by_category[c["inferred_category"]].append(c)

        all_ranked: list[dict[str, Any]] = []
        for group in by_category.values():
            all_ranked.extend(rank_candidates(group))

        # UPSERT one EvaluationRecord per candidate
        movies_processed += 1
        total_candidates += len(all_ranked)
        for ranked in all_ranked:
            if ranked.get("would_replace_current_choice"):
                total_would_replace += 1
            repo.upsert_evaluation(EvaluationRecord(
                info_hash=ranked["info_hash"],
                movie_href=ranked.get("movie_href", movie_href),
                scoring_version=scoring_version,
                javdb_category=ranked.get("javdb_category"),
                magnet_name=ranked.get("magnet_name"),
                inferred_category=ranked.get("inferred_category"),
                category_consistent=ranked.get("category_consistent"),
                subtitle_evidence=ranked.get("subtitle_evidence"),
                resolution_consistent=ranked.get("resolution_consistent"),
                score=ranked["score"],
                shadow_rank=ranked["shadow_rank"],
                would_replace_current_choice=ranked["would_replace_current_choice"],
                policy_mode="assist",
                decision=ranked["decision"],
                reasons=list(ranked.get("reasons") or []),
            ))

    return {
        "movies": movies_processed,
        "candidates": total_candidates,
        "would_replace": total_would_replace,
    }


def run_assist(*, days: int = 2) -> dict[str, int]:
    """Production wiring for the ADR-024 assist evaluator.

    Opens REPORTS_DB_PATH, resolves recent movie hrefs from evaluations created in
    the last ``days``, then calls evaluate_assist_for_movies. Assist reads/writes
    D1 only — no qBittorrent or proxy — so it takes no proxy/category arguments.
    """
    from datetime import datetime, timedelta, timezone

    from javdb.infra.config import cfg
    from javdb.storage.db import REPORTS_DB_PATH, get_db
    from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

    if days <= 0:
        # A non-positive window would produce a future/empty cutoff and silently
        # no-op — fail fast so a bad --days is visible.
        raise ValueError("days must be positive")

    # Honour the look-back window: only re-rank movies whose evaluations were
    # created within the last ``days`` (day-granularity ISO string compare).
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with get_db(REPORTS_DB_PATH) as conn:
        repo = TorrentQualityRepo(conn)

        recent = repo.list_recent_evaluations(limit=500, since=since)
        movie_hrefs = list(dict.fromkeys(
            r["movie_href"] for r in recent if r.get("movie_href")
        ))

        if not movie_hrefs:
            logger.info(
                "No evaluations in the last %d day(s); assist evaluator is a no-op.",
                days,
            )
            return {"movies": 0, "candidates": 0, "would_replace": 0}

        # Read via cfg() — the policy mode lives in config.py / VAR_ env, not as a
        # module attribute on javdb.infra.config (a getattr there silently falls
        # through to 'shadow', defeating the gate for direct callers).
        policy_mode = cfg("TORRENT_QUALITY_POLICY_MODE", "shadow")

        return evaluate_assist_for_movies(
            movie_hrefs,
            repo=repo,
            policy_mode=policy_mode,
        )
