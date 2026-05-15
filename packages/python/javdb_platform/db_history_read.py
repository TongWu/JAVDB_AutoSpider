"""History record reading for JAVDB AutoSpider.

Handles reading from MovieHistory and TorrentHistory tables in history.db.

The history tables store cumulative records of all movies and torrents that
have been processed, used for incremental scraping (avoiding re-downloads).
"""

from typing import Dict, Optional

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_HISTORY_DB_PATH = None
_load_history_joined = None
_pending_movie_overlay = None
_pending_torrent_overlay = None
_batch_update_movie_actors = None


def _ensure_imports():
    """Lazy import to avoid circular dependency with db_connection."""
    global _get_db, _HISTORY_DB_PATH
    global _load_history_joined, _pending_movie_overlay, _pending_torrent_overlay
    global _batch_update_movie_actors
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import (
                get_db,
                HISTORY_DB_PATH,
            )
            from packages.python.javdb_platform.db_layer.history_repo import (
                load_history_joined,
                batch_update_movie_actors,
            )
            _get_db = get_db
            _HISTORY_DB_PATH = HISTORY_DB_PATH
            _load_history_joined = load_history_joined
            _batch_update_movie_actors = batch_update_movie_actors

            # Import pending overlay helpers from db.py (will be moved later)
            from packages.python.javdb_platform.db import (
                _pending_movie_overlay,
                _pending_torrent_overlay,
            )
            globals()['_pending_movie_overlay'] = _pending_movie_overlay
            globals()['_pending_torrent_overlay'] = _pending_torrent_overlay
        except ImportError:
            # Fallback to db.py during Phase 1
            from packages.python.javdb_platform.db import (
                get_db,
                HISTORY_DB_PATH,
                _load_history_joined as load_hist,
                _pending_movie_overlay as pmo,
                _pending_torrent_overlay as pto,
                _batch_update_movie_actors as batch_actors,
            )
            _get_db = get_db
            _HISTORY_DB_PATH = HISTORY_DB_PATH
            _load_history_joined = load_hist
            _pending_movie_overlay = pmo
            _pending_torrent_overlay = pto
            _batch_update_movie_actors = batch_actors


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
        movie_overlay = _pending_movie_overlay(conn, session_id)
        torrent_overlay = _pending_torrent_overlay(conn, session_id)

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
        return _batch_update_movie_actors(conn, updates, session_id)


# ── Delegating wrappers (pending full migration) ────────────────────────


def db_batch_update_last_visited(*args, **kwargs):
    """Update DateTimeVisited for a batch of hrefs. Delegates to db.py."""
    from packages.python.javdb_platform.db import db_batch_update_last_visited as _f
    return _f(*args, **kwargs)


def db_check_torrent_in_history(*args, **kwargs):
    """Check if a specific torrent type exists for href. Delegates to db.py."""
    from packages.python.javdb_platform.db import db_check_torrent_in_history as _f
    return _f(*args, **kwargs)
