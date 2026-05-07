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
from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_core.contracts import indicators_to_category as _indicators_to_category


def _execute_backend_batch(conn, statements):
    if not statements:
        return []
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        return batch(statements)
    cursors = []
    for sql, params in statements:
        cursors.append(conn.execute(sql, params))
    return cursors


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
    session_id: Optional[int] = None,
    audit_record_movie_change=None,
    audit_movie_change_statement=None,
) -> int:
    """Batch update actor fields using executemany.

    Entries with no meaningful actor data (empty name/link and only ``'[]'``
    supporting actors) are silently skipped to avoid overwriting existing
    good data with empty values.

    When *session_id* is set, each affected MovieHistory row also gets
    ``SessionId=?`` and a companion ``MovieHistoryAudit`` row capturing
    the prior state. The audit callbacks are injected from
    :mod:`packages.python.javdb_platform.db` to avoid an import cycle.
    """
    if not updates:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base_url = cfg('BASE_URL', 'https://javdb.com')
    payload = []
    href_pair_lookup: List[Tuple[str, str]] = []
    for href, an, ag, al, sup in updates:
        if not _has_meaningful_actor_data(an, al, sup):
            continue
        path_href, abs_href = movie_href_lookup_values(href, base_url)
        if not path_href and not abs_href:
            path_href = href
            abs_href = href
        href_pair_lookup.append((path_href, abs_href))
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
        if audit_movie_change_statement is None:
            if audit_record_movie_change is not None:
                # Snapshot pre-update rows for audit. We record one audit row per
                # affected MovieHistory.Id; since several updates could resolve to
                # the same Href pair, dedupe by Id.
                seen_ids = set()
                for path_href, abs_href in href_pair_lookup:
                    old_rows = conn.execute(
                        "SELECT * FROM MovieHistory WHERE Href IN (?, ?)",
                        (path_href, abs_href),
                    ).fetchall()
                    for row in old_rows:
                        rid = row['Id']
                        if rid in seen_ids:
                            continue
                        seen_ids.add(rid)
                        audit_record_movie_change(
                            conn, rid, action='UPDATE', session_id=session_id,
                            old_row=row, when=now,
                        )
            before = conn.total_changes
            # Tag each updated row with SessionId. Build an extended payload
            # that puts session_id alongside the other UPDATE values.
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

        total = 0
        update_sql = """
            UPDATE MovieHistory
            SET ActorName=?, ActorGender=?, ActorLink=?, SupportingActors=?,
                DateTimeUpdated=?, SessionId=?
            WHERE Href IN (?, ?)
            """
        batch_size = 20
        for start in range(0, len(payload), batch_size):
            payload_chunk = payload[start:start + batch_size]
            href_chunk = href_pair_lookup[start:start + batch_size]
            seen_ids = set()
            audit_rows = []
            for path_href, abs_href in href_chunk:
                old_rows = conn.execute(
                    "SELECT * FROM MovieHistory WHERE Href IN (?, ?)",
                    (path_href, abs_href),
                ).fetchall()
                for row in old_rows:
                    rid = row['Id']
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    audit_rows.append(row)
            statements = [
                (
                    update_sql,
                    (an, ag, al, sup, now_v, session_id, p, a),
                )
                for (an, ag, al, sup, now_v, p, a) in payload_chunk
            ]
            for row in audit_rows:
                audit_stmt = audit_movie_change_statement(
                    row['Id'],
                    action='UPDATE',
                    session_id=session_id,
                    old_row=row,
                    when=now,
                )
                if audit_stmt is not None:
                    statements.append(audit_stmt)
            cursors = _execute_backend_batch(conn, statements)
            total += sum((cur.rowcount or 0) for cur in cursors[:len(payload_chunk)])
        return total

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
