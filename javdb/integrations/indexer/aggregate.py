"""Cross-source dedup + ADR-024 live quality merge (ADR-054 WS3)."""

from __future__ import annotations

import unicodedata
from typing import Any

from javdb.integrations.indexer import dispatch
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.qb.client import extract_hash_from_magnet
from javdb.quality.features import extract_file_features
from javdb.quality.scoring import score_torrent

_PROBE_UNAVAILABLE = "probe_unavailable"


def _collect(video_code: str) -> list[IndexerResult]:
    return dispatch.aggregate(video_code)


def _normalize_video_code(video_code: str) -> str:
    return unicodedata.normalize("NFKC", video_code or "").strip().upper()


def _normalize_info_hash(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized or len(normalized) != 40 or not all(c in "0123456789abcdef" for c in normalized):
        return None
    return normalized


def _info_hash(magnet: IndexerMagnet) -> str | None:
    return _normalize_info_hash(magnet.info_hash) or extract_hash_from_magnet(
        magnet.magnet_uri
    )


def _javdb_category(tags: list[str]) -> str:
    known = {"subtitle", "hacked_subtitle", "no_subtitle", "hacked_no_subtitle"}
    for tag in tags:
        normalized = str(tag).strip()
        if normalized in known:
            return normalized
    return ""


def _score(magnet: IndexerMagnet) -> tuple[float, list[str]]:
    tags = list(magnet.tags or [])
    result = score_torrent(
        extract_file_features([]),
        {
            "magnet_name": magnet.name,
            "javdb_tags": tags,
            "javdb_category": _javdb_category(tags),
        },
    )
    reasons = list(result.get("reasons") or [])
    if _PROBE_UNAVAILABLE not in reasons:
        reasons.append(_PROBE_UNAVAILABLE)
    return float(result.get("score", 0.0)), reasons


def _row(magnet: IndexerMagnet, info_hash: str | None) -> dict[str, Any]:
    score, reasons = _score(magnet)
    return {
        "magnet_uri": magnet.magnet_uri,
        "name": magnet.name,
        "size": magnet.size,
        "tags": list(magnet.tags or []),
        "file_count": magnet.file_count,
        "info_hash": info_hash,
        "sources": [magnet.source],
        "quality_score": score,
        "quality_reasons": reasons,
    }


def aggregate_magnets(video_code: str) -> list[dict[str, Any]]:
    """Return deduped, ADR-024-scored magnet rows across all active sources."""
    fallback_code = _normalize_video_code(video_code)
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for result in _collect(video_code):
        if not result.ok:
            continue

        for raw_magnet in result.magnets:
            source = (raw_magnet.source or result.source).strip()
            if not source:
                source = result.source
            normalized_magnet = IndexerMagnet(
                magnet_uri=raw_magnet.magnet_uri,
                name=raw_magnet.name,
                source=source,
                info_hash=raw_magnet.info_hash,
                size=raw_magnet.size,
                tags=list(raw_magnet.tags or []),
                file_count=raw_magnet.file_count,
            )
            info_hash = _info_hash(normalized_magnet)
            key = ("info_hash", info_hash) if info_hash else ("video_code", fallback_code)
            candidate = _row(normalized_magnet, info_hash)
            existing = rows_by_key.get(key)
            if existing is None:
                rows_by_key[key] = candidate
                continue

            sources = set(existing["sources"])
            sources.update(candidate["sources"])
            if candidate["quality_score"] > existing["quality_score"]:
                candidate["sources"] = sorted(sources)
                rows_by_key[key] = candidate
            else:
                existing["sources"] = sorted(sources)

    return sorted(
        rows_by_key.values(),
        key=lambda row: row["quality_score"],
        reverse=True,
    )
