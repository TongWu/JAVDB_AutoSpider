"""Repository for ADR-024 torrent quality tables (Phase 1).

Conn-injected direct-UPSERT access to `TorrentQualityEvidence` and
`TorrentQualityEvaluation` on the canonical D1 `reports` database. These
evidence/enrichment tables are outside the `MovieHistory` / `TorrentHistory`
Pending->Commit history staging flow; callers write them through a live
`get_db(REPORTS_DB_PATH)` transaction, not via `db_stage_history_write()`. Follows
the `AcquisitionOutcomeRepo` (ADR-033) pattern: `__init__(self, conn)` so a caller
(e.g. the IMP-05 collector) can write one torrent's evidence + N evaluations in a
single transaction; a single `_*_COLUMNS` tuple drives INSERT columns, value
extraction, and row->dict.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional

from javdb.quality.models import EvaluationRecord, EvidenceRecord

logger = logging.getLogger(__name__)

# Promoted, queryable evidence columns - `EvidenceRecord.features` may not reuse
# these names (otherwise one fact would live in both a column and features_json).
_PROMOTED_EVIDENCE_KEYS = frozenset(
    {
        "probe_target_name",
        "metadata_status",
        "metadata_started_at",
        "metadata_completed_at",
        "total_size_bytes",
        "main_video_size_bytes",
        "main_video_ratio",
        "video_file_count",
        "subtitle_file_count",
        "non_video_file_count",
        "junk_size_bytes",
        "junk_size_ratio",
        "suspicious_file_count",
        "source_fingerprint",
    }
)

_EVIDENCE_COLUMNS = (
    "info_hash",
    "probe_schema_version",
    "target_role",
    "probe_target_name",
    "metadata_status",
    "metadata_started_at",
    "metadata_completed_at",
    "total_size_bytes",
    "main_video_size_bytes",
    "main_video_ratio",
    "video_file_count",
    "subtitle_file_count",
    "non_video_file_count",
    "junk_size_bytes",
    "junk_size_ratio",
    "suspicious_file_count",
    "features_json",
    "reasons_json",
    "source_fingerprint",
)
_EVIDENCE_PK = ("info_hash", "probe_schema_version", "target_role")

_EVALUATION_COLUMNS = (
    "info_hash",
    "movie_href",
    "scoring_version",
    "video_code",
    "javdb_category",
    "magnet_name",
    "javdb_tags_json",
    "javdb_size_text",
    "inferred_category",
    "category_consistent",
    "subtitle_evidence",
    "resolution_consistent",
    "source_trust",
    "score",
    "shadow_rank",
    "would_replace_current_choice",
    "policy_mode",
    "decision",
    "reasons_json",
)
_EVALUATION_PK = ("info_hash", "movie_href", "scoring_version")
_JSON_COLUMNS = frozenset({"features_json", "javdb_tags_json", "reasons_json"})


def _bool_to_int(value: Optional[bool]) -> Optional[int]:
    return None if value is None else (1 if value else 0)


def _upsert_sql(table: str, columns: tuple[str, ...], pk: tuple[str, ...]) -> str:
    placeholders = ", ".join(["?"] * len(columns))
    conflict = ", ".join(pk)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in pk)
    updates = f"{updates}, updated_at=(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )


def _evidence_values(rec: EvidenceRecord) -> list[Any]:
    overlap = _PROMOTED_EVIDENCE_KEYS & set(rec.features)
    if overlap:
        raise ValueError(
            "EvidenceRecord.features must not duplicate promoted columns: "
            f"{sorted(overlap)}"
        )
    return [
        rec.info_hash,
        rec.probe_schema_version,
        rec.target_role,
        rec.probe_target_name,
        rec.metadata_status,
        rec.metadata_started_at,
        rec.metadata_completed_at,
        rec.total_size_bytes,
        rec.main_video_size_bytes,
        rec.main_video_ratio,
        rec.video_file_count,
        rec.subtitle_file_count,
        rec.non_video_file_count,
        rec.junk_size_bytes,
        rec.junk_size_ratio,
        rec.suspicious_file_count,
        json.dumps(rec.features, ensure_ascii=False),
        json.dumps(rec.reasons, ensure_ascii=False),
        rec.source_fingerprint,
    ]


def _evaluation_values(rec: EvaluationRecord) -> list[Any]:
    return [
        rec.info_hash,
        rec.movie_href,
        rec.scoring_version,
        rec.video_code,
        rec.javdb_category,
        rec.magnet_name,
        json.dumps(rec.javdb_tags, ensure_ascii=False),
        rec.javdb_size_text,
        rec.inferred_category,
        _bool_to_int(rec.category_consistent),
        rec.subtitle_evidence,
        _bool_to_int(rec.resolution_consistent),
        rec.source_trust,
        rec.score,
        rec.shadow_rank,
        _bool_to_int(rec.would_replace_current_choice),
        rec.policy_mode,
        rec.decision,
        json.dumps(rec.reasons, ensure_ascii=False),
    ]


class TorrentQualityRepo:
    """Conn-injected read/write access to the ADR-024 quality tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert_evidence(self, record: EvidenceRecord) -> None:
        self._conn.execute(
            _upsert_sql("TorrentQualityEvidence", _EVIDENCE_COLUMNS, _EVIDENCE_PK),
            _evidence_values(record),
        )

    def get_evidence(
        self, info_hash: str, probe_schema_version: str, target_role: str
    ) -> Optional[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVIDENCE_COLUMNS)} FROM TorrentQualityEvidence "
            "WHERE info_hash = ? AND probe_schema_version = ? AND target_role = ?"
        )
        row = self._conn.execute(
            sql, (info_hash, probe_schema_version, target_role)
        ).fetchone()
        return self._to_dict(row, _EVIDENCE_COLUMNS) if row else None

    def upsert_evaluation(self, record: EvaluationRecord) -> None:
        self._conn.execute(
            _upsert_sql(
                "TorrentQualityEvaluation", _EVALUATION_COLUMNS, _EVALUATION_PK
            ),
            _evaluation_values(record),
        )

    def list_evaluations_for_movie(self, movie_href: str) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLUMNS)} FROM TorrentQualityEvaluation "
            "WHERE movie_href = ? ORDER BY shadow_rank ASC"
        )
        rows = self._conn.execute(sql, (movie_href,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLUMNS) for r in rows]

    def list_recent_evaluations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = int(limit)
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql = (
            f"SELECT {', '.join(_EVALUATION_COLUMNS)} FROM TorrentQualityEvaluation "
            "ORDER BY created_at DESC, info_hash DESC, movie_href DESC, "
            "scoring_version DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._to_dict(r, _EVALUATION_COLUMNS) for r in rows]

    @staticmethod
    def _to_dict(row: Mapping[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for col in columns:
            value = TorrentQualityRepo._decode_json_value(col, row[col])
            key = col[:-5] if col.endswith("_json") else col
            data[key] = value
        return data

    @staticmethod
    def _decode_json_value(column: str, value: Any) -> Any:
        if column not in _JSON_COLUMNS or value is None or value == "":
            return value
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            logger.debug("failed to decode JSON column %s", column, exc_info=True)
            return value
