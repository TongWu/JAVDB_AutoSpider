"""History-related SQLite helpers used by `utils.infra.db`."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from apps.api.parsers.common import (
    normalize_javdb_href_path,
    movie_href_lookup_values,
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from javdb.infra.config import cfg
from javdb.spider.contracts import indicators_to_category as _indicators_to_category


def load_history_joined(conn) -> Dict[str, dict]:
    """Load MovieHistory+TorrentHistory in one LEFT JOIN query."""
    rows = conn.execute(
        """
        SELECT
            m.Id AS MovieId,
            m.VideoCode,
            m.Href,
            m.ActorName,
            m.ActorGender,
            m.ActorLink,
            m.SupportingActors,
            m.DateTimeCreated AS MovieDateTimeCreated,
            m.DateTimeUpdated AS MovieDateTimeUpdated,
            m.DateTimeVisited,
            m.PerfectMatchIndicator,
            m.HiResIndicator,
            t.SubtitleIndicator,
            t.CensorIndicator,
            t.MagnetUri,
            t.Size,
            t.FileCount,
            t.ResolutionType,
            t.DateTimeCreated AS TorrentDateTimeCreated,
            t.DateTimeUpdated AS TorrentDateTimeUpdated
        FROM MovieHistory m
        LEFT JOIN TorrentHistory t ON t.MovieHistoryId = m.Id
        ORDER BY m.Id
        """
    ).fetchall()

    history: Dict[str, dict] = {}
    for row in rows:
        r = dict(row)
        href = normalize_javdb_href_path(r["Href"]) or r["Href"]
        item = history.get(href)
        if item is None:
            item = {
                "VideoCode": r.get("VideoCode", ""),
                "DateTimeCreated": r.get("MovieDateTimeCreated", ""),
                "DateTimeUpdated": r.get("MovieDateTimeUpdated", ""),
                "DateTimeVisited": r.get("DateTimeVisited", ""),
                "PerfectMatchIndicator": bool(r.get("PerfectMatchIndicator", 0)),
                "HiResIndicator": bool(r.get("HiResIndicator", 0)),
                "ActorName": r.get("ActorName"),
                "ActorGender": r.get("ActorGender"),
                "ActorLink": r.get("ActorLink"),
                "SupportingActors": r.get("SupportingActors"),
                "torrent_types": [],
                "torrents": {},
            }
            history[href] = item

        if r.get("SubtitleIndicator") is None or r.get("CensorIndicator") is None:
            continue

        sub = int(r["SubtitleIndicator"])
        cen = int(r["CensorIndicator"])
        cat = _indicators_to_category(sub, cen)
        item["torrent_types"].append(cat)
        item["torrents"][(sub, cen)] = {
            "MagnetUri": r.get("MagnetUri", ""),
            "Size": r.get("Size", ""),
            "FileCount": r.get("FileCount", 0),
            "ResolutionType": r.get("ResolutionType"),
            "DateTimeCreated": r.get("TorrentDateTimeCreated", ""),
            "DateTimeUpdated": r.get("TorrentDateTimeUpdated", ""),
        }
    return history


def _has_meaningful_actor_data(an: str, al: str, sup: str) -> bool:
    """True when at least one actor field carries real content (not just ``'[]'``)."""
    if an.strip():
        return True
    if al.strip():
        return True
    s = sup.strip()
    return bool(s and s != '[]')


def batch_update_movie_actors(
    conn,
    updates: List[Tuple[str, str, str, str, str]],
    *,
    session_id: Optional[str] = None,
) -> int:
    """Batch update actor fields using executemany.

    Entries with no meaningful actor data (empty name/link and only ``'[]'``
    supporting actors) are silently skipped to avoid overwriting existing
    good data with empty values.

    When *session_id* is set, each affected MovieHistory row also gets
    ``SessionId=?`` stamped.
    """
    if not updates:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base_url = cfg('BASE_URL', 'https://javdb.com')
    payload = []
    for href, an, ag, al, sup in updates:
        if not _has_meaningful_actor_data(an, al, sup):
            continue
        path_href, abs_href = movie_href_lookup_values(href, base_url)
        if not path_href and not abs_href:
            path_href = href
            abs_href = href
        payload.append((
            an,
            ag,
            javdb_absolute_url(al, base_url) if al else al,
            absolutize_supporting_actors_json(sup, base_url) if sup else sup,
            now,
            path_href,
            abs_href,
        ))
    if not payload:
        return 0

    if session_id is not None:
        before = conn.total_changes
        ext_payload = [
            (an, ag, al, sup, now_v, session_id, p, a)
            for (an, ag, al, sup, now_v, p, a) in payload
        ]
        conn.executemany(
            """
            UPDATE MovieHistory
            SET ActorName=?, ActorGender=?, ActorLink=?, SupportingActors=?,
                DateTimeUpdated=?, SessionId=?
            WHERE Href IN (?, ?)
            """,
            ext_payload,
        )
        return conn.total_changes - before

    before = conn.total_changes
    conn.executemany(
        """
        UPDATE MovieHistory
        SET ActorName=?, ActorGender=?, ActorLink=?, SupportingActors=?, DateTimeUpdated=?
        WHERE Href IN (?, ?)
        """,
        payload,
    )
    return conn.total_changes - before


# ── HistoryRepo (ADR-005 PR-1) ────────────────────────────────────────
#
# A typed surface over the write-domain function family in
# ``javdb/storage/db/db_history_read.py`` and
# ``db_history_write.py``. PR-1 is purely additive: every method is a
# thin delegate to the existing ``db_*`` function. Callers can adopt
# ``HistoryRepo`` at their own pace; the function family stays the
# source of truth until ADR-005 PR-2 inlines the SQL here and retires
# the functions.
#
# Pattern: ``HistoryRepo(*, db_path=None)`` per ADR-005 amendment 2 —
# the underlying function family already opens its own conn from
# ``db_path``; the Repo carries no per-call state. ``session_id`` flows
# explicitly through every method that needs it (D5 goal preserved).


class HistoryRepo:
    """Thin typed wrapper over the History domain (`history.db`).

    Method-for-method delegation to the ``db_*`` function family in
    ``javdb/storage/db/db_history_*.py``. Establishes the interface
    surface so callers can migrate incrementally; PR-2 will inline the
    SQL here and retire the underlying functions.

    Construction takes only an optional ``db_path`` override (used in
    tests / smoke runs against a fresh DB). Methods that mutate state
    take ``session_id`` per call so a single Repo instance can service
    multiple sessions (e.g. a sweep over stale runs) without rebuild.
    """

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    # ── Reads (no session_id required) ────────────────────────────

    def load_history(
        self, *, phase: Optional[int] = None,
    ) -> Dict[str, dict]:
        """Load full MovieHistory + TorrentHistory state, keyed by Href."""
        from javdb.storage.db import db_load_history
        return db_load_history(db_path=self._db_path, phase=phase)

    def load_history_snapshot(
        self, session_id: Optional[str],
    ) -> Dict[str, dict]:
        """Load history + this session's Pending overlay (for spider reads)."""
        from javdb.storage.db import db_load_history_snapshot
        return db_load_history_snapshot(
            session_id=session_id, db_path=self._db_path,
        )

    def check_torrent_in_history(
        self, href: str, torrent_type: str,
    ) -> bool:
        """True iff ``(href, torrent_type)`` already lives in TorrentHistory."""
        from javdb.storage.db import db_check_torrent_in_history
        return db_check_torrent_in_history(
            href=href, torrent_type=torrent_type, db_path=self._db_path,
        )

    def get_all_history_records(self) -> list:
        """All MovieHistory rows as plain dicts (forensic / export use)."""
        from javdb.storage.db import db_get_all_history_records
        return db_get_all_history_records(db_path=self._db_path)

    # ── Writes (session_id required) ──────────────────────────────

    def stage_history_write(
        self, session_id: str, kind: str, payload: Dict,
    ) -> str:
        """Append a pending movie/torrent history row. Returns Seq."""
        from javdb.storage.db import db_stage_history_write
        return db_stage_history_write(
            session_id=session_id, kind=kind, payload=payload,
            db_path=self._db_path,
        )

    def stage_movie(self, session_id: str, payload: Dict) -> str:
        """Append a row to PendingMovieHistoryWrites. Returns Seq."""
        return self.stage_history_write(session_id, "movie", payload)

    def stage_torrent(self, session_id: str, payload: Dict) -> str:
        """Append a row to PendingTorrentHistoryWrites. Returns Seq."""
        return self.stage_history_write(session_id, "torrent", payload)

    def commit_session(self, session_id: str, **kwargs) -> dict:
        """Drain Pending* tables into live MovieHistory / TorrentHistory."""
        from javdb.storage.db import db_commit_session_history
        return db_commit_session_history(session_id, **kwargs)

    def batch_update_last_visited(self, hrefs: List[str]) -> int:
        """Bump LastVisited on each href; staging-aware under pending mode."""
        from javdb.storage.db import db_batch_update_last_visited
        return db_batch_update_last_visited(hrefs, db_path=self._db_path)

    def batch_update_movie_actors(
        self, updates: List[Tuple[str, str, str, str, str]],
    ) -> int:
        """Bulk overwrite actor fields, preserving pending-mode staging."""
        # The db.py facade owns pending-mode staging for actor-only writes.
        from javdb.storage.db import db_batch_update_movie_actors
        return db_batch_update_movie_actors(updates, db_path=self._db_path)
