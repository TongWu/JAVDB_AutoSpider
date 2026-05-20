"""History-related SQLite helpers used by `utils.infra.db`."""

from __future__ import annotations

import base64
import csv
import io
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

from apps.api.parsers.common import (
    normalize_javdb_href_path,
    movie_href_lookup_values,
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from javdb.infra.config import cfg
from javdb.spider.contracts import indicators_to_category as _indicators_to_category


def _normalize_date_bound(value: str, *, is_end: bool) -> str:
    """Normalize a date/datetime string to the DB format ``YYYY-MM-DD HH:MM:SS``.

    Accepts:
    - Full ISO 8601 datetime: ``2026-01-01T10:00:00Z``, ``2026-01-01T10:00:00``,
      with optional timezone offset or fractional seconds.
    - Space-separated datetime already in DB format: ``2026-01-01 10:00:00``.
    - Date-only: ``2026-01-01``.  ``is_end=False`` → ``00:00:00``;
      ``is_end=True`` → ``23:59:59`` (inclusive of the whole day).

    Raises:
        ValueError: If the input cannot be parsed as a recognisable date/datetime.
    """
    v = value.strip()
    # Date-only: exactly 10 chars matching YYYY-MM-DD, no time component
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        try:
            dt = datetime.strptime(v, "%Y-%m-%d")
            if is_end:
                return dt.strftime("%Y-%m-%d 23:59:59")
            return dt.strftime("%Y-%m-%d 00:00:00")
        except ValueError:
            pass
    # Strip trailing Z before fromisoformat (Python <3.11 doesn't accept it)
    if v.endswith("Z"):
        v = v[:-1]
    # Replace T separator so fromisoformat handles both styles
    v = v.replace("T", " ")
    # Drop timezone offset (e.g. +05:30) — keep only the datetime portion
    # A '+' or '-' after position 10 indicates a timezone offset.
    for sep in ("+", "-"):
        pos = v.find(sep, 10)
        if pos != -1:
            v = v[:pos]
            break
    try:
        dt = datetime.fromisoformat(v)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ValueError(f"invalid date: {value!r}")


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
    session_id: Optional[str] = None,
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
    :mod:`javdb.storage.db.db` to avoid an import cycle.
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


# ── Filter helpers (Issue 3) ──────────────────────────────────────────


def _build_movie_filters(
    *,
    q: Optional[str] = None,
    actor: Optional[str] = None,
    perfect_match: Optional[bool] = None,
    hi_res: Optional[bool] = None,
    session_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    cursor_id: Optional[int] = None,
) -> Tuple[str, List]:
    """Build the WHERE clause and params list for MovieHistory queries.

    The ``cursor_id`` arg is the already-decoded integer Id (not the raw
    base64 cursor); callers must decode the cursor before calling.

    Raises:
        ValueError: Propagated from ``_normalize_date_bound`` on bad dates.
    """
    wheres: List[str] = []
    params: List = []

    if cursor_id is not None:
        wheres.append("m.Id > ?")
        params.append(cursor_id)

    if q is not None:
        like = f"%{q}%"
        wheres.append(
            "(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?)"
        )
        params.extend([like, like, like])

    if actor is not None:
        wheres.append("m.ActorName = ?")
        params.append(actor)

    if perfect_match is not None:
        wheres.append("m.PerfectMatchIndicator = ?")
        params.append(1 if perfect_match else 0)

    if hi_res is not None:
        wheres.append("m.HiResIndicator = ?")
        params.append(1 if hi_res else 0)

    if session_id is not None:
        wheres.append("m.SessionId = ?")
        params.append(session_id)

    if date_from is not None:
        wheres.append("m.DateTimeCreated >= ?")
        params.append(_normalize_date_bound(date_from, is_end=False))

    if date_to is not None:
        wheres.append("m.DateTimeCreated <= ?")
        params.append(_normalize_date_bound(date_to, is_end=True))

    where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    return where_clause, params


def _build_torrent_filters(
    *,
    q: Optional[str] = None,
    resolution_type: Optional[int] = None,
    has_subtitle: Optional[bool] = None,
    uncensored: Optional[bool] = None,
    session_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    cursor_id: Optional[int] = None,
) -> Tuple[str, List]:
    """Build the WHERE clause and params list for TorrentHistory queries.

    Raises:
        ValueError: Propagated from ``_normalize_date_bound`` on bad dates.
    """
    wheres: List[str] = []
    params: List = []

    if cursor_id is not None:
        wheres.append("t.Id > ?")
        params.append(cursor_id)

    if q is not None:
        like = f"%{q}%"
        wheres.append("m.VideoCode LIKE ?")
        params.append(like)

    if resolution_type is not None:
        wheres.append("t.ResolutionType = ?")
        params.append(resolution_type)

    if has_subtitle is not None:
        wheres.append("t.SubtitleIndicator = ?")
        params.append(1 if has_subtitle else 0)

    if uncensored is not None:
        if uncensored:
            wheres.append("t.CensorIndicator = 0")
        else:
            wheres.append("t.CensorIndicator != 0")

    if session_id is not None:
        wheres.append("t.SessionId = ?")
        params.append(session_id)

    if date_from is not None:
        wheres.append("t.DateTimeCreated >= ?")
        params.append(_normalize_date_bound(date_from, is_end=False))

    if date_to is not None:
        wheres.append("t.DateTimeCreated <= ?")
        params.append(_normalize_date_bound(date_to, is_end=True))

    where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    return where_clause, params


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
        from javdb.storage.db.db_history_read import db_load_history
        return db_load_history(db_path=self._db_path, phase=phase)

    def load_history_snapshot(self, session_id: str) -> Dict[str, dict]:
        """Load history + this session's Pending overlay (for spider reads)."""
        from javdb.storage.db.db_history_read import db_load_history_snapshot
        return db_load_history_snapshot(
            session_id=session_id, db_path=self._db_path,
        )

    def check_torrent_in_history(
        self, href: str, torrent_type: str,
    ) -> bool:
        """True iff ``(href, torrent_type)`` already lives in TorrentHistory."""
        from javdb.storage.db.db_history_read import db_check_torrent_in_history
        return db_check_torrent_in_history(
            href=href, torrent_type=torrent_type, db_path=self._db_path,
        )

    def get_all_history_records(self) -> list:
        """All MovieHistory rows as plain dicts (forensic / export use)."""
        from javdb.storage.db.db_history_read import db_get_all_history_records
        return db_get_all_history_records(db_path=self._db_path)

    # ── Writes (session_id required) ──────────────────────────────

    def stage_movie(self, session_id: str, payload: Dict) -> str:
        """Append a row to PendingMovieHistoryWrites. Returns Seq."""
        from javdb.storage.db.db_history_write import db_stage_history_write
        return db_stage_history_write(
            session_id=session_id, kind="movie", payload=payload,
            db_path=self._db_path,
        )

    def stage_torrent(self, session_id: str, payload: Dict) -> str:
        """Append a row to PendingTorrentHistoryWrites. Returns Seq."""
        from javdb.storage.db.db_history_write import db_stage_history_write
        return db_stage_history_write(
            session_id=session_id, kind="torrent", payload=payload,
            db_path=self._db_path,
        )

    def commit_session(self, session_id: str, **kwargs) -> dict:
        """Drain Pending* tables into live MovieHistory / TorrentHistory."""
        from javdb.storage.db.db_history_write import db_commit_session_history
        return db_commit_session_history(session_id, **kwargs)

    def batch_update_last_visited(self, hrefs: List[str]) -> int:
        """Bump LastVisited on each href; staging-aware under pending mode."""
        from javdb.storage.db.db_history_read import db_batch_update_last_visited
        return db_batch_update_last_visited(hrefs, db_path=self._db_path)

    def batch_update_movie_actors(
        self, updates: List[Tuple[str, str, str, str]],
    ) -> int:
        """Bulk overwrite (ActorName, Gender, Link, SupportingActorsJson)."""
        from javdb.storage.db.db_history_read import db_batch_update_movie_actors
        return db_batch_update_movie_actors(updates, db_path=self._db_path)

    # ── Search / export (Phase 2, Task 1) ────────────────────────────

    def search_movies(
        self,
        *,
        q: Optional[str] = None,
        actor: Optional[str] = None,
        perfect_match: Optional[bool] = None,
        hi_res: Optional[bool] = None,
        session_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Tuple[List[dict], Optional[str], int]:
        """Search MovieHistory with optional filters and keyset pagination.

        Returns (items, next_cursor, total_estimate).
        - items: list of dicts with DB column names + torrent_count.
        - next_cursor: base64-encoded Id of the last returned row, or None.
        - total_estimate: COUNT(*) with same WHERE, capped at 10000.

        Raises:
            ValueError: On malformed cursor or unparseable date bounds.
        """
        from javdb.storage.db.db_connection import get_db, HISTORY_DB_PATH

        cursor_id: Optional[int] = None
        if cursor is not None:
            try:
                cursor_id = int(base64.b64decode(cursor).decode())
            except Exception:
                raise ValueError("invalid cursor")

        where_clause, params = _build_movie_filters(
            q=q,
            actor=actor,
            perfect_match=perfect_match,
            hi_res=hi_res,
            session_id=session_id,
            date_from=date_from,
            date_to=date_to,
            cursor_id=cursor_id,
        )

        count_sql = f"SELECT MIN(COUNT(*), 10000) FROM MovieHistory m {where_clause}"
        data_sql = f"""
            SELECT
                m.Id,
                m.VideoCode,
                m.Href,
                m.ActorName,
                m.ActorGender,
                m.SupportingActors,
                m.PerfectMatchIndicator,
                m.HiResIndicator,
                m.DateTimeCreated,
                m.DateTimeUpdated,
                m.SessionId,
                COUNT(t.Id) AS torrent_count
            FROM MovieHistory m
            LEFT JOIN TorrentHistory t ON t.MovieHistoryId = m.Id
            {where_clause}
            GROUP BY m.Id
            ORDER BY m.Id
            LIMIT ?
        """

        fetch_limit = limit + 1
        with get_db(self._db_path or HISTORY_DB_PATH) as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(data_sql, params + [fetch_limit]).fetchall()

        items = [dict(r) for r in rows]
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor: Optional[str] = None
        if has_more and items:
            next_cursor = base64.b64encode(str(items[-1]["Id"]).encode()).decode()

        return items, next_cursor, int(total)

    def search_torrents(
        self,
        *,
        q: Optional[str] = None,
        resolution_type: Optional[int] = None,
        has_subtitle: Optional[bool] = None,
        uncensored: Optional[bool] = None,
        session_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Tuple[List[dict], Optional[str], int]:
        """Search TorrentHistory (JOINed with MovieHistory) with keyset pagination.

        Returns (items, next_cursor, total_estimate).

        Raises:
            ValueError: On malformed cursor or unparseable date bounds.
        """
        from javdb.storage.db.db_connection import get_db, HISTORY_DB_PATH

        cursor_id: Optional[int] = None
        if cursor is not None:
            try:
                cursor_id = int(base64.b64decode(cursor).decode())
            except Exception:
                raise ValueError("invalid cursor")

        where_clause, params = _build_torrent_filters(
            q=q,
            resolution_type=resolution_type,
            has_subtitle=has_subtitle,
            uncensored=uncensored,
            session_id=session_id,
            date_from=date_from,
            date_to=date_to,
            cursor_id=cursor_id,
        )

        count_sql = f"""
            SELECT MIN(COUNT(*), 10000)
            FROM TorrentHistory t
            JOIN MovieHistory m ON m.Id = t.MovieHistoryId
            {where_clause}
        """
        data_sql = f"""
            SELECT
                t.Id,
                m.VideoCode AS movie_video_code,
                m.Href AS movie_href,
                t.MagnetUri,
                t.Size,
                t.SubtitleIndicator,
                t.CensorIndicator,
                t.ResolutionType,
                t.FileCount,
                t.DateTimeCreated,
                t.SessionId
            FROM TorrentHistory t
            JOIN MovieHistory m ON m.Id = t.MovieHistoryId
            {where_clause}
            ORDER BY t.Id
            LIMIT ?
        """

        fetch_limit = limit + 1
        with get_db(self._db_path or HISTORY_DB_PATH) as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(data_sql, params + [fetch_limit]).fetchall()

        items = [dict(r) for r in rows]
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor: Optional[str] = None
        if has_more and items:
            next_cursor = base64.b64encode(str(items[-1]["Id"]).encode()).decode()

        return items, next_cursor, int(total)

    def export_movies_csv(
        self,
        *,
        q: Optional[str] = None,
        actor: Optional[str] = None,
        perfect_match: Optional[bool] = None,
        hi_res: Optional[bool] = None,
        session_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Iterator[str]:
        """Yield CSV rows for MovieHistory (header first, no pagination limit).

        Each yielded string is one CSV line (newline included).
        Rows are streamed lazily — the DB connection stays open during iteration.

        Raises:
            ValueError: On unparseable date bounds.
        """
        from javdb.storage.db.db_connection import get_db, HISTORY_DB_PATH

        where_clause, params = _build_movie_filters(
            q=q,
            actor=actor,
            perfect_match=perfect_match,
            hi_res=hi_res,
            session_id=session_id,
            date_from=date_from,
            date_to=date_to,
        )

        sql = f"""
            SELECT
                m.Id,
                m.VideoCode,
                m.Href,
                m.ActorName,
                m.ActorGender,
                m.SupportingActors,
                m.PerfectMatchIndicator,
                m.HiResIndicator,
                m.DateTimeCreated,
                m.DateTimeUpdated,
                m.SessionId,
                COUNT(t.Id) AS torrent_count
            FROM MovieHistory m
            LEFT JOIN TorrentHistory t ON t.MovieHistoryId = m.Id
            {where_clause}
            GROUP BY m.Id
            ORDER BY m.Id
        """

        columns = [
            "Id", "VideoCode", "Href", "ActorName", "ActorGender",
            "SupportingActors", "PerfectMatchIndicator", "HiResIndicator",
            "DateTimeCreated", "DateTimeUpdated", "SessionId", "torrent_count",
        ]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()

        with get_db(self._db_path or HISTORY_DB_PATH) as conn:
            for row in conn.execute(sql, params):
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([dict(row).get(c) for c in columns])
                yield buf.getvalue()

    def export_torrents_csv(
        self,
        *,
        q: Optional[str] = None,
        resolution_type: Optional[int] = None,
        has_subtitle: Optional[bool] = None,
        uncensored: Optional[bool] = None,
        session_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Iterator[str]:
        """Yield CSV rows for TorrentHistory (header first, no pagination limit).

        Each yielded string is one CSV line (newline included).
        Rows are streamed lazily — the DB connection stays open during iteration.

        Raises:
            ValueError: On unparseable date bounds.
        """
        from javdb.storage.db.db_connection import get_db, HISTORY_DB_PATH

        where_clause, params = _build_torrent_filters(
            q=q,
            resolution_type=resolution_type,
            has_subtitle=has_subtitle,
            uncensored=uncensored,
            session_id=session_id,
            date_from=date_from,
            date_to=date_to,
        )

        sql = f"""
            SELECT
                t.Id,
                m.VideoCode AS movie_video_code,
                m.Href AS movie_href,
                t.MagnetUri,
                t.Size,
                t.SubtitleIndicator,
                t.CensorIndicator,
                t.ResolutionType,
                t.FileCount,
                t.DateTimeCreated,
                t.SessionId
            FROM TorrentHistory t
            JOIN MovieHistory m ON m.Id = t.MovieHistoryId
            {where_clause}
            ORDER BY t.Id
        """

        columns = [
            "Id", "movie_video_code", "movie_href", "MagnetUri", "Size",
            "SubtitleIndicator", "CensorIndicator", "ResolutionType",
            "FileCount", "DateTimeCreated", "SessionId",
        ]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()

        with get_db(self._db_path or HISTORY_DB_PATH) as conn:
            for row in conn.execute(sql, params):
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([dict(row).get(c) for c in columns])
                yield buf.getvalue()
