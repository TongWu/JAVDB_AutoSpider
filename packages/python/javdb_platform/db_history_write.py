"""History record writing for JAVDB AutoSpider.

Handles writing to MovieHistory and TorrentHistory tables in history.db.

Supports two write modes:
- 'pending' (default) — Stage writes to Pending* tables, commit in bulk
- 'audit' (legacy) — Direct upsert with audit trail for rollback

The pending mode is the recommended approach for new workflows.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_HISTORY_DB_PATH = None
_generate_session_id = None
_get_active_run_identity = None
_SESSION_ID_PATTERN = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _get_db, _HISTORY_DB_PATH, _generate_session_id
    global _get_active_run_identity, _SESSION_ID_PATTERN
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import (
                get_db,
                HISTORY_DB_PATH,
            )
            from packages.python.javdb_platform.db_session import (
                generate_session_id,
                get_active_run_identity,
                SESSION_ID_PATTERN,
            )
            _get_db = get_db
            _HISTORY_DB_PATH = HISTORY_DB_PATH
            _generate_session_id = generate_session_id
            _get_active_run_identity = get_active_run_identity
            _SESSION_ID_PATTERN = SESSION_ID_PATTERN
        except ImportError:
            # Fallback to db.py during Phase 1
            from packages.python.javdb_platform.db import (
                get_db,
                HISTORY_DB_PATH,
                _generate_session_id as gen_sid,
                get_active_run_identity as get_run_id,
                _SESSION_ID_PATTERN as sid_pattern,
            )
            _get_db = get_db
            _HISTORY_DB_PATH = HISTORY_DB_PATH
            _generate_session_id = gen_sid
            _get_active_run_identity = get_run_id
            _SESSION_ID_PATTERN = sid_pattern


# Constants
_PENDING_KINDS = {'movie', 'torrent'}
_KIND_MOVIE = 'movie'
_KIND_TORRENT = 'torrent'


def category_to_indicators(category: str) -> Tuple[int, int]:
    """Map category name to (SubtitleIndicator, CensorIndicator)."""
    mapping = {
        'hacked_subtitle': (1, 0),
        'hacked_no_subtitle': (0, 0),
        'subtitle': (1, 1),
        'no_subtitle': (0, 1),
    }
    return mapping.get(category, (0, 0))


def indicators_to_category(sub_ind: int, cen_ind: int) -> str:
    """Map (SubtitleIndicator, CensorIndicator) to category name."""
    if sub_ind == 1 and cen_ind == 0:
        return 'hacked_subtitle'
    elif sub_ind == 0 and cen_ind == 0:
        return 'hacked_no_subtitle'
    elif sub_ind == 1 and cen_ind == 1:
        return 'subtitle'
    else:
        return 'no_subtitle'


# ── Pending mode (recommended) ───────────────────────────────────────────


def db_stage_history_write(
    session_id: str,
    kind: str,
    payload: Dict[str, Any],
    *,
    db_path: Optional[str] = None,
) -> str:
    """Append a row to PendingMovie/TorrentHistoryWrites.

    Args:
        session_id: Session identifier
        kind: 'movie' or 'torrent'
        payload: Row data dict (flexible keys)
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        Seq value (TEXT snowflake)

    Raises:
        ValueError: If kind is invalid or payload is missing required fields
    """
    _ensure_imports()

    if kind not in _PENDING_KINDS:
        raise ValueError(
            f"db_stage_history_write: kind must be one of {_PENDING_KINDS}, "
            f"got {kind!r}"
        )
    if not payload.get("Href") and not payload.get("href"):
        raise ValueError("db_stage_history_write: payload requires 'Href'")

    href = payload.get("Href") or payload.get("href")
    video_code = payload.get("VideoCode") or payload.get("video_code")
    visited = (
        payload.get("DateTimeVisited")
        or payload.get("date_time_visited")
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = _get_active_run_identity()
    seq = _generate_session_id()

    # Validate seq format
    if not _SESSION_ID_PATTERN.match(seq):
        raise ValueError(
            f"db_stage_history_write: refusing to INSERT with Seq={seq!r} "
            f"(expected a TEXT snowflake from generate_session_id)"
        )

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        if kind == _KIND_MOVIE:
            conn.execute(
                """INSERT INTO PendingMovieHistoryWrites
                   (Seq, SessionId, RunId, RunAttempt, Href, VideoCode,
                    ActorName, ActorGender, ActorLink, SupportingActors,
                    DateTimeVisited, CreatedAt, ApplyState)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    seq,
                    session_id,
                    run_id,
                    run_attempt,
                    href,
                    video_code,
                    payload.get("ActorName") or payload.get("actor_name"),
                    payload.get("ActorGender") or payload.get("actor_gender"),
                    payload.get("ActorLink") or payload.get("actor_link"),
                    (
                        payload.get("SupportingActors")
                        or payload.get("supporting_actors")
                    ),
                    visited,
                    now,
                ),
            )
        else:  # torrent
            sub_ind = payload.get("SubtitleIndicator")
            cen_ind = payload.get("CensorIndicator")
            category = payload.get("Category") or payload.get("category")
            if sub_ind is None or cen_ind is None:
                if not category:
                    raise ValueError(
                        "db_stage_history_write(torrent): payload needs "
                        "either Category or (SubtitleIndicator, CensorIndicator)"
                    )
                sub_ind, cen_ind = category_to_indicators(category)
            if not category:
                category = indicators_to_category(int(sub_ind), int(cen_ind))
            conn.execute(
                """INSERT INTO PendingTorrentHistoryWrites
                   (Seq, SessionId, RunId, RunAttempt, Href, VideoCode,
                    Category, SubtitleIndicator, CensorIndicator,
                    MagnetUri, Size, FileCount, ResolutionType,
                    DateTimeVisited, CreatedAt, ApplyState)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    seq,
                    session_id,
                    run_id,
                    run_attempt,
                    href,
                    video_code,
                    category,
                    int(sub_ind),
                    int(cen_ind),
                    payload.get("MagnetUri") or payload.get("magnet_uri"),
                    payload.get("Size") or payload.get("size"),
                    int(payload.get("FileCount") or payload.get("file_count") or 0),
                    payload.get("ResolutionType") or payload.get("resolution_type"),
                    visited,
                    now,
                ),
            )
    return seq


def db_commit_session_history(
    session_id: str,
    db_path: Optional[str] = None,
) -> Tuple[int, int]:
    """Commit all pending writes for a session.

    Bulk-commits PendingMovieHistoryWrites and PendingTorrentHistoryWrites
    into MovieHistory and TorrentHistory.

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        Tuple of (movies_committed, torrents_committed)
    """
    _ensure_imports()

    # Import commit logic from db.py (will be refactored later)
    from packages.python.javdb_platform.db import _commit_session_bulk

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        return _commit_session_bulk(conn, session_id)


# ── Audit mode (legacy) ──────────────────────────────────────────────────


def db_upsert_history(
    href: str,
    movie_data: dict,
    torrent_data: List[dict],
    session_id: Optional[str] = None,
) -> None:
    """Legacy audit-mode upsert (deprecated, use pending mode).

    Args:
        href: Movie href
        movie_data: Movie metadata dict
        torrent_data: List of torrent dicts
        session_id: Session identifier (optional)
    """
    _ensure_imports()

    # Import upsert logic from db.py (will be refactored later)
    from packages.python.javdb_platform.db import (
        _db_upsert_history_impl,
    )

    with _get_db(_HISTORY_DB_PATH) as conn:
        _db_upsert_history_impl(conn, href, movie_data, torrent_data, session_id)


# ── Rollback interface ───────────────────────────────────────────────────


def rollback_history_for_session(
    session_id: str,
    db_path: Optional[str] = None,
) -> int:
    """Rollback history writes for a session.

    Handles both pending mode (delete from Pending* tables) and
    audit mode (restore from *Audit tables).

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        Number of rows rolled back
    """
    _ensure_imports()

    # Import rollback logic from db.py (will be refactored later)
    from packages.python.javdb_platform.db import _rollback_history

    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        return _rollback_history(conn, session_id)
