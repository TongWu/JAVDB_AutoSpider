"""ADR-024 Phase 1 production-download shadow-only read-only orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features
from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.quality.scoring import SCORING_VERSION, score_torrent

logger = logging.getLogger(__name__)

PRODUCTION_TARGET_ROLE = "production_download"


def _normalize_hash(value: Any) -> str:
    return str(value or "").strip().lower()


def collect_production_evidence(
    *,
    torrents: list[dict],
    fetch_files: Callable[[str], Optional[list]],
    repo: Any,
    context_for: Callable[[dict], dict],
    probe_target_name: str = "production",
) -> dict[str, int]:
    """Collect evidence and shadow evaluations for production torrents."""
    summary = {
        "scanned": 0,
        "skipped": 0,
        "evidence_written": 0,
        "evaluations_written": 0,
        "probe_unavailable": 0,
    }

    for torrent in torrents:
        summary["scanned"] += 1
        info_hash = (torrent.get("hash") or "").strip()
        if not info_hash:
            summary["skipped"] += 1
            continue

        context = context_for(torrent)
        files = fetch_files(info_hash)

        if not files:
            repo.upsert_evidence(
                EvidenceRecord(
                    info_hash=info_hash,
                    probe_schema_version=PROBE_SCHEMA_VERSION,
                    target_role=PRODUCTION_TARGET_ROLE,
                    probe_target_name=probe_target_name,
                    metadata_status="probe_unavailable",
                    reasons=["probe_unavailable"],
                )
            )
            summary["evidence_written"] += 1
            summary["probe_unavailable"] += 1
            continue

        feats = extract_file_features(files)
        repo.upsert_evidence(
            EvidenceRecord(
                info_hash=info_hash,
                probe_schema_version=PROBE_SCHEMA_VERSION,
                target_role=PRODUCTION_TARGET_ROLE,
                probe_target_name=probe_target_name,
                metadata_status="metadata_received",
                total_size_bytes=feats["total_size_bytes"],
                main_video_size_bytes=feats["main_video_size_bytes"],
                main_video_ratio=feats["main_video_ratio"],
                video_file_count=feats["video_file_count"],
                subtitle_file_count=feats["subtitle_file_count"],
                non_video_file_count=feats["non_video_file_count"],
                junk_size_bytes=feats["junk_size_bytes"],
                junk_size_ratio=feats["junk_size_ratio"],
                suspicious_file_count=feats["suspicious_file_count"],
                features={"main_video_name": feats["main_video_name"]},
            )
        )
        summary["evidence_written"] += 1

        scored = score_torrent(feats, context)
        repo.upsert_evaluation(
            EvaluationRecord(
                info_hash=info_hash,
                movie_href=context.get("movie_href", ""),
                scoring_version=SCORING_VERSION,
                video_code=context.get("video_code"),
                javdb_category=context.get("javdb_category"),
                magnet_name=context.get("magnet_name"),
                javdb_tags=list(context.get("javdb_tags", []) or []),
                javdb_size_text=context.get("javdb_size_text"),
                inferred_category=scored["inferred_category"],
                category_consistent=scored["category_consistent"],
                subtitle_evidence=scored["subtitle_evidence"],
                resolution_consistent=scored["resolution_consistent"],
                score=scored["score"],
                shadow_rank=None,
                would_replace_current_choice=False,
                policy_mode="shadow",
                decision=scored["decision"],
                reasons=list(scored["reasons"] or []),
            )
        )
        summary["evaluations_written"] += 1

    logger.info(
        "Quality evidence: scanned=%d evidence=%d evaluations=%d probe_unavailable=%d skipped=%d",
        summary["scanned"],
        summary["evidence_written"],
        summary["evaluations_written"],
        summary["probe_unavailable"],
        summary["skipped"],
    )
    return summary


def _build_context(torrent: dict, outcome: Any) -> dict:
    """Build shadow-scoring context from ADR-033 acquisition outcome data."""
    magnet_name = torrent.get("name")
    if outcome is not None:
        return {
            "movie_href": getattr(outcome, "href", "") or "",
            "video_code": getattr(outcome, "video_code", None),
            "javdb_category": getattr(outcome, "category", None),
            "magnet_name": magnet_name,
            "javdb_tags": [],
            "javdb_size_text": None,
        }
    return {
        "movie_href": "",
        "video_code": None,
        "javdb_category": None,
        "magnet_name": magnet_name,
        "javdb_tags": [],
        "javdb_size_text": None,
    }


def run_collection(
    *,
    days: int = 2,
    categories: Optional[list[str]] = None,
    use_proxy=None,
) -> dict[str, int]:
    """Production wiring for the ADR-024 shadow evidence collector."""
    if not categories:
        logger.warning(
            "Skipping quality evidence collection: no categories configured; "
            "refusing to scan all production qBittorrent categories"
        )
        return {
            "scanned": 0,
            "skipped": 0,
            "evidence_written": 0,
            "evaluations_written": 0,
            "probe_unavailable": 0,
        }

    import requests

    from javdb.integrations.qb import readonly
    from javdb.integrations.qb.file_filter import service as ff
    from javdb.storage.db import OPERATIONS_DB_PATH, REPORTS_DB_PATH, get_db
    # Prime reconcile package import order before loading the repo class.
    import javdb.ops.reconcile.service  # noqa: F401
    from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
    from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

    ff.initialize_proxy_helper(use_proxy)
    if not ff.test_qbittorrent_connection(use_proxy):
        raise RuntimeError("Cannot connect to qBittorrent")

    session = requests.Session()
    try:
        if not ff.login_to_qbittorrent(session, use_proxy):
            raise RuntimeError("Failed to login to qBittorrent")

        torrents = ff.get_recent_torrents(
            session,
            days=days,
            categories=categories,
            use_proxy=use_proxy,
        )
        if not torrents:
            return {
                "scanned": 0,
                "skipped": 0,
                "evidence_written": 0,
                "evaluations_written": 0,
                "probe_unavailable": 0,
            }

        readonly.wait_for_metadata_readiness(
            torrents,
            fetch_files=lambda h: ff.get_torrent_files(session, h, use_proxy),
        )

        outcomes: dict[str, Any] = {}
        with get_db(OPERATIONS_DB_PATH) as ops_conn:
            acq_repo = AcquisitionOutcomeRepo(ops_conn)
            for torrent in torrents:
                info_hash = (torrent.get("hash") or "").strip()
                if not info_hash:
                    continue
                normalized_hash = _normalize_hash(info_hash)
                outcome = acq_repo.get(normalized_hash) or acq_repo.get(info_hash)
                if outcome is not None:
                    outcomes[normalized_hash] = outcome

        with get_db(REPORTS_DB_PATH) as conn:
            repo = TorrentQualityRepo(conn)
            return collect_production_evidence(
                torrents=torrents,
                fetch_files=lambda h: ff.get_torrent_files(session, h, use_proxy),
                repo=repo,
                context_for=lambda t: _build_context(
                    t,
                    outcomes.get(_normalize_hash(t.get("hash"))),
                ),
            )
    finally:
        session.close()
