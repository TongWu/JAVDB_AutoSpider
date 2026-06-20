"""ADR-024 IMP-08: TorrentQualityReviewLabel repository (conn-injected).

D1 is the source of truth; this repo is the read/write port for operator
accept/reject labels over shadow quality evaluations — the labelled dataset
Phase 3 tunes thresholds against.

Follows the TorrentProbeRepo (ADR-024 IMP-10) pattern: conn-injected,
row_factory=sqlite3.Row, no conn.commit() inside the repo — commits are left
to the get_db() context manager.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class ReviewLabel:
    info_hash: str
    movie_href: str
    scoring_version: str
    label: str
    reviewer: Optional[str] = None
    note: Optional[str] = None


class TorrentQualityReviewRepo:
    """Conn-injected read/write access to TorrentQualityReviewLabel."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert_label(self, label: ReviewLabel, *, reviewed_at: str) -> None:
        """Insert or update an operator review label (idempotent on PK conflict)."""
        self._conn.execute(
            """
            INSERT INTO TorrentQualityReviewLabel (
                info_hash, movie_href, scoring_version,
                label, reviewer, note, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(info_hash, movie_href, scoring_version) DO UPDATE SET
                label=excluded.label,
                reviewer=excluded.reviewer,
                note=excluded.note,
                reviewed_at=excluded.reviewed_at
            """,
            (
                label.info_hash,
                label.movie_href,
                label.scoring_version,
                label.label,
                label.reviewer,
                label.note,
                reviewed_at,
            ),
        )

    def list_labels(
        self,
        *,
        movie_href: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Return review labels, optionally filtered by movie_href.

        Results are ordered by reviewed_at DESC. Returns plain dicts.
        """
        sql = (
            "SELECT info_hash, movie_href, scoring_version, label, "
            "reviewer, note, reviewed_at "
            "FROM TorrentQualityReviewLabel"
        )
        params: List[Any] = []
        if movie_href is not None:
            sql += " WHERE movie_href = ?"
            params.append(movie_href)
        sql += " ORDER BY reviewed_at DESC"
        if limit is not None:
            limit = int(limit)
            if limit <= 0:
                # A negative LIMIT means "no limit" in SQLite — reject it so
                # pagination can't be silently bypassed.
                raise ValueError("limit must be positive")
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
