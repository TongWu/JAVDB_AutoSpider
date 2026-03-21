"""History-related SQLite helpers used by `utils.db`."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from utils.contracts import indicators_to_category as _indicators_to_category


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
        href = r["Href"]
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


def batch_update_movie_actors(conn, updates: List[Tuple[str, str, str, str, str]]) -> int:
    """Batch update actor fields using executemany."""
    if not updates:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = [(an, ag, al, sup, now, href) for href, an, ag, al, sup in updates]
    before = conn.total_changes
    conn.executemany(
        """
        UPDATE MovieHistory
        SET ActorName=?, ActorGender=?, ActorLink=?, SupportingActors=?, DateTimeUpdated=?
        WHERE Href=?
        """,
        payload,
    )
    return conn.total_changes - before

