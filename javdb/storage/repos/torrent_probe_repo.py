"""ADR-024 IMP-10: TorrentProbeCandidate queue repository (conn-injected).

D1 is the source of truth; this repo is the read/write port used by both the
capture step (enqueue) and the remote probe runner (list_pending / mark_status).
Follows the AcquisitionOutcomeRepo (ADR-033) pattern: conn-injected, no
conn.commit() inside the repo — commits are left to the get_db() context manager.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ProbeCandidate:
    info_hash: str
    movie_href: str
    magnet_uri: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    javdb_tags: List[str] = field(default_factory=list)
    javdb_size_text: Optional[str] = None


class TorrentProbeRepo:
    """Conn-injected read/write access to TorrentProbeCandidate queue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def enqueue(self, cand: ProbeCandidate, *, enqueued_at: str) -> None:
        """Insert or update a probe candidate (idempotent on PK conflict)."""
        self._conn.execute(
            """
            INSERT INTO TorrentProbeCandidate (
                info_hash, movie_href, video_code, javdb_category, magnet_uri,
                magnet_name, javdb_tags_json, javdb_size_text, status, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(info_hash, movie_href) DO UPDATE SET
                video_code=excluded.video_code,
                javdb_category=excluded.javdb_category,
                magnet_uri=excluded.magnet_uri,
                magnet_name=excluded.magnet_name,
                javdb_tags_json=excluded.javdb_tags_json,
                javdb_size_text=excluded.javdb_size_text
            """,
            (
                cand.info_hash, cand.movie_href, cand.video_code,
                cand.javdb_category, cand.magnet_uri, cand.magnet_name,
                json.dumps(cand.javdb_tags or [], ensure_ascii=False),
                cand.javdb_size_text, enqueued_at,
            ),
        )

    def list_pending(self, *, limit: Optional[int] = None) -> List[ProbeCandidate]:
        """Return pending candidates ordered by enqueued_at (oldest first)."""
        sql = (
            "SELECT info_hash, movie_href, video_code, javdb_category, magnet_uri, "
            "magnet_name, javdb_tags_json, javdb_size_text "
            "FROM TorrentProbeCandidate WHERE status='pending' ORDER BY enqueued_at"
        )
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            ProbeCandidate(
                info_hash=r["info_hash"],
                movie_href=r["movie_href"],
                video_code=r["video_code"],
                javdb_category=r["javdb_category"],
                magnet_uri=r["magnet_uri"],
                magnet_name=r["magnet_name"],
                javdb_tags=json.loads(r["javdb_tags_json"]) if r["javdb_tags_json"] else [],
                javdb_size_text=r["javdb_size_text"],
            )
            for r in rows
        ]

    def mark_status(
        self, info_hash: str, movie_href: str, status: str, *, probed_at: str
    ) -> None:
        """Update status (and probed_at) for a specific (info_hash, movie_href)."""
        self._conn.execute(
            "UPDATE TorrentProbeCandidate SET status=?, probed_at=? "
            "WHERE info_hash=? AND movie_href=?",
            (status, probed_at, info_hash, movie_href),
        )
