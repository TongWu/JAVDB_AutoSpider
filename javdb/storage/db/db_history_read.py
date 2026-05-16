"""History record reading for JAVDB AutoSpider.

Handles reading from MovieHistory and TorrentHistory tables in history.db.

The history tables store cumulative records of all movies and torrents that
have been processed, used for incremental scraping (avoiding re-downloads).
"""

from typing import Any, Dict, Iterable, Optional, Tuple

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_HISTORY_DB_PATH = None
_load_history_joined = None
_batch_update_movie_actors = None
_category_to_indicators = None
_movie_href_lookup_values = None
_cfg = None


def _ensure_imports():
    """Lazy import to avoid circular dependency with db_connection."""
    global _get_db, _HISTORY_DB_PATH
    global _load_history_joined, _batch_update_movie_actors
    global _category_to_indicators, _movie_href_lookup_values, _cfg
    if _get_db is None:
        from javdb.storage.db.db_connection import (
            get_db,
            HISTORY_DB_PATH,
        )
        from javdb.storage.repos.history_repo import (
            load_history_joined,
            batch_update_movie_actors,
        )
        from javdb.spider.contracts import category_to_indicators
        from apps.api.parsers.common import movie_href_lookup_values
        from javdb.infra.config import cfg
        _get_db = get_db
        _HISTORY_DB_PATH = HISTORY_DB_PATH
        _load_history_joined = load_history_joined
        _batch_update_movie_actors = batch_update_movie_actors
        _category_to_indicators = category_to_indicators
        _movie_href_lookup_values = movie_href_lookup_values
        _cfg = cfg


# ── History loading ──────────────────────────────────────────────────────


def db_load_history(
    db_path: Optional[str] = None,
    phase: Optional[int] = None,
) -> Dict[str, dict]:
    """Load history from MovieHistory + TorrentHistory into a dict keyed by Href.

    The phase parameter is accepted for backward compatibility but ignored
    (the new schema does not store phase).

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)
        phase: Ignored (backward compatibility)

    Returns:
        Dict mapping href to movie dict with torrent data
    """
    _ensure_imports()

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        return _load_history_joined(conn)


def db_load_history_snapshot(
    session_id: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, dict]:
    """Return committed-live history with the session_id pending overlay.

    When session_id is None, returns just the committed live state
    (equivalent to load_history_joined). Otherwise, the pending
    rows for that session shadow the live values per Href / per torrent
    type, giving the caller a "what would we see if we committed right
    now" view without polluting other sessions' reads.

    Args:
        session_id: Session identifier (None for live state only)
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        Dict mapping href to movie dict with torrent data (including pending)
    """
    _ensure_imports()

    def indicators_to_category(sub_ind: int, cen_ind: int) -> str:
        """Map (SubtitleIndicator, CensorIndicator) to category name."""
        if sub_ind == 1 and cen_ind == 0:
            return 'hacked_subtitle'
        elif sub_ind == 0 and cen_ind == 0:
            return 'hacked_no_subtitle'
        elif sub_ind == 1 and cen_ind == 1:
            return 'subtitle'
        else:  # sub_ind == 0 and cen_ind == 1
            return 'no_subtitle'

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        snapshot = _load_history_joined(conn)
        if session_id is None:
            return snapshot
        movie_overlay = _pending_movie_overlay_impl(conn, session_id)
        torrent_overlay = _pending_torrent_overlay_impl(conn, session_id)

    # Merge movie overlay
    for href, row in movie_overlay.items():
        item = snapshot.get(href)
        if item is None:
            item = {
                "VideoCode": row.get("VideoCode") or "",
                "DateTimeCreated": row.get("CreatedAt") or "",
                "DateTimeUpdated": row.get("CreatedAt") or "",
                "DateTimeVisited": row.get("DateTimeVisited") or "",
                "PerfectMatchIndicator": False,
                "HiResIndicator": False,
                "ActorName": row.get("ActorName"),
                "ActorGender": row.get("ActorGender"),
                "ActorLink": row.get("ActorLink"),
                "SupportingActors": row.get("SupportingActors"),
                "torrent_types": [],
                "torrents": {},
            }
            snapshot[href] = item
        else:
            for col in (
                "VideoCode", "ActorName", "ActorGender",
                "ActorLink", "SupportingActors",
            ):
                if row.get(col) is not None:
                    item[col] = row.get(col)
            if row.get("DateTimeVisited"):
                item["DateTimeVisited"] = row["DateTimeVisited"]

    # Merge torrent overlay
    for (href, sub, cen), row in torrent_overlay.items():
        item = snapshot.get(href)
        if item is None:
            item = {
                "VideoCode": row.get("VideoCode") or "",
                "DateTimeCreated": row.get("CreatedAt") or "",
                "DateTimeUpdated": row.get("CreatedAt") or "",
                "DateTimeVisited": row.get("DateTimeVisited") or "",
                "PerfectMatchIndicator": False,
                "HiResIndicator": False,
                "ActorName": None,
                "ActorGender": None,
                "ActorLink": None,
                "SupportingActors": None,
                "torrent_types": [],
                "torrents": {},
            }
            snapshot[href] = item
        cat = indicators_to_category(int(sub), int(cen))
        if cat not in item["torrent_types"]:
            item["torrent_types"].append(cat)
        item["torrents"][(int(sub), int(cen))] = {
            "MagnetUri": row.get("MagnetUri") or "",
            "Size": row.get("Size") or "",
            "FileCount": row.get("FileCount") or 0,
            "ResolutionType": row.get("ResolutionType"),
            "DateTimeCreated": row.get("CreatedAt") or "",
            "DateTimeUpdated": row.get("CreatedAt") or "",
        }

    # Recompute the derived indicators so callers see the same value the
    # commit step would land in MovieHistory.PerfectMatchIndicator /
    # HiResIndicator. Live-only callers (session_id=None) skip this.
    for item in snapshot.values():
        torrents = item.get("torrents", {})
        item["PerfectMatchIndicator"] = bool(
            (1, 0) in torrents and (1, 1) in torrents
        )
        item["HiResIndicator"] = any(
            (t.get("ResolutionType") or 0) >= 2560
            for t in torrents.values()
        )

    return snapshot


# ── Actor updates ────────────────────────────────────────────────────────


def db_batch_update_movie_actors(
    updates: list,
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Batch update actor columns in MovieHistory.

    Args:
        updates: List of (href, actor_dict) tuples
        session_id: Session identifier (optional)
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        Number of rows updated
    """
    _ensure_imports()

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        return _batch_update_movie_actors(conn, updates, session_id=session_id)


# ── Pending overlay helpers ──────────────────────────────────────────────


def _merge_movie_overlay_rows(rows: Iterable[Any]) -> Dict[str, dict]:
    """Merge pending-movie rows (Seq-ascending order) into a sparse overlay."""
    overlay: Dict[str, dict] = {}
    for row in rows:
        d = dict(row)
        key = d["Href"]
        existing = overlay.get(key)
        if existing is None:
            d["_merged_seqs"] = [d["Seq"]]
            overlay[key] = d
            continue
        existing["_merged_seqs"].append(d["Seq"])
        for col, value in d.items():
            if col == "_merged_seqs":
                continue
            if col == "Seq":
                existing[col] = value
                continue
            if value is not None:
                existing[col] = value
    return overlay


def _merge_torrent_overlay_rows(
    rows: Iterable[Any],
) -> Dict[Tuple[str, int, int], dict]:
    """Merge pending-torrent rows (Seq-ascending) into a sparse overlay."""
    overlay: Dict[Tuple[str, int, int], dict] = {}
    for row in rows:
        d = dict(row)
        key = (
            d["Href"],
            int(d["SubtitleIndicator"]),
            int(d["CensorIndicator"]),
        )
        existing = overlay.get(key)
        if existing is None:
            d["_merged_seqs"] = [d["Seq"]]
            overlay[key] = d
            continue
        existing["_merged_seqs"].append(d["Seq"])
        for col, value in d.items():
            if col == "_merged_seqs":
                continue
            if col == "Seq":
                existing[col] = value
                continue
            if value is not None:
                existing[col] = value
    return overlay


def _pending_movie_overlay_impl(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[str, dict]:
    """Return ``{href: merged_pending_movie_row}`` for *session_id*."""
    placeholders = ",".join("?" for _ in include_states)
    params: list = [session_id]
    params.extend(include_states)
    where_extra = ""
    if href is not None:
        where_extra = " AND Href=?"
        params.append(href)
    sql = (
        "SELECT * FROM PendingMovieHistoryWrites "
        f"WHERE SessionId=? AND ApplyState IN ({placeholders}){where_extra}"
        " ORDER BY Seq ASC"
    )
    return _merge_movie_overlay_rows(conn.execute(sql, params).fetchall())


def _pending_torrent_overlay_impl(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[Tuple[str, int, int], dict]:
    """Return ``{(href, sub, cen): merged_pending_torrent_row}`` for *session_id*."""
    placeholders = ",".join("?" for _ in include_states)
    params: list = [session_id]
    params.extend(include_states)
    where_extra = ""
    if href is not None:
        where_extra = " AND Href=?"
        params.append(href)
    sql = (
        "SELECT * FROM PendingTorrentHistoryWrites "
        f"WHERE SessionId=? AND ApplyState IN ({placeholders}){where_extra}"
        " ORDER BY Seq ASC"
    )
    return _merge_torrent_overlay_rows(conn.execute(sql, params).fetchall())


# ── Torrent history check ───────────────────────────────────────────────


def db_check_torrent_in_history(
    href: str, torrent_type: str, db_path: Optional[str] = None,
) -> bool:
    """Check if a specific torrent type exists for href."""
    _ensure_imports()
    sub_ind, cen_ind = _category_to_indicators(torrent_type)
    base_url = _cfg('BASE_URL', 'https://javdb.com')
    path_href, abs_href = _movie_href_lookup_values(href, base_url)
    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        if path_href and abs_href:
            row = conn.execute(
                """
                SELECT t.MagnetUri FROM TorrentHistory t
                JOIN MovieHistory m ON t.MovieHistoryId = m.Id
                WHERE m.Href IN (?, ?)
                  AND t.SubtitleIndicator = ? AND t.CensorIndicator = ?
                """,
                (path_href, abs_href, sub_ind, cen_ind),
            ).fetchone()
        else:
            lookup = path_href or abs_href or href
            row = conn.execute(
                """
                SELECT t.MagnetUri FROM TorrentHistory t
                JOIN MovieHistory m ON t.MovieHistoryId = m.Id
                WHERE m.Href = ? AND t.SubtitleIndicator = ? AND t.CensorIndicator = ?
                """,
                (lookup, sub_ind, cen_ind),
            ).fetchone()
        if row is None:
            return False
        return bool(row['MagnetUri'] and row['MagnetUri'].startswith('magnet:'))


# ── All history records (migration support) ─────────────────────────────


def db_get_all_history_records(db_path: Optional[str] = None) -> list:
    """Return all MovieHistory records as dicts (for migration verification)."""
    _ensure_imports()
    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM MovieHistory ORDER BY Id").fetchall()
        return [dict(r) for r in rows]


# ── Delegating wrappers (pending full migration) ────────────────────────


def db_batch_update_last_visited(*args, **kwargs):
    """Update DateTimeVisited for a batch of hrefs. Delegates to db.py."""
    from javdb.storage.db.db import db_batch_update_last_visited as _f
    return _f(*args, **kwargs)
