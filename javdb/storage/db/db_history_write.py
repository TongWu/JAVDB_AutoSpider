"""History record writing for JAVDB AutoSpider.

Handles writing to MovieHistory and TorrentHistory tables in history.db.

Uses pending mode: stage writes to Pending* tables, then commit in bulk
via db_commit_session_history().
"""

import os
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from javdb.infra.config import cfg
from javdb.infra.logging import get_logger
from javdb.spider.contracts import (
    category_to_indicators as _category_to_indicators,
)

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_HISTORY_DB_PATH = None
_REPORTS_DB_PATH = None
_generate_session_id = None
_get_active_run_identity = None
_SESSION_ID_PATTERN = None
_resolve_session_id = None
_get_active_write_mode = None
_execute_backend_batch = None
_movie_href_lookup_values = None
_javdb_absolute_url = None
_absolutize_supporting_actors_json = None
_has_meaningful_actor_data = None
_batch_update_movie_actors_repo = None
_generate_integer_id = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _get_db, _HISTORY_DB_PATH, _REPORTS_DB_PATH, _generate_session_id
    global _get_active_run_identity, _SESSION_ID_PATTERN
    global _resolve_session_id, _get_active_write_mode
    global _execute_backend_batch
    global _movie_href_lookup_values, _javdb_absolute_url
    global _absolutize_supporting_actors_json
    global _has_meaningful_actor_data, _batch_update_movie_actors_repo
    global _generate_integer_id
    if _get_db is None:
        from javdb.storage.db.db_connection import (
            get_db,
            HISTORY_DB_PATH,
            REPORTS_DB_PATH,
            _execute_backend_batch as ebb,
        )
        from javdb.storage.db.db_session import (
            generate_session_id,
            get_active_run_identity,
            SESSION_ID_PATTERN,
            _resolve_session_id as rsi,
            get_active_write_mode,
        )
        from apps.api.parsers.common import (
            movie_href_lookup_values,
            javdb_absolute_url,
            absolutize_supporting_actors_json,
        )
        from javdb.storage.repos.history_repo import (
            _has_meaningful_actor_data as hmad,
            batch_update_movie_actors as buam,
        )
        from javdb.storage.db.db_session import generate_integer_id as gii
        _get_db = get_db
        _HISTORY_DB_PATH = HISTORY_DB_PATH
        _REPORTS_DB_PATH = REPORTS_DB_PATH
        _generate_session_id = generate_session_id
        _get_active_run_identity = get_active_run_identity
        _SESSION_ID_PATTERN = SESSION_ID_PATTERN
        _resolve_session_id = rsi
        _get_active_write_mode = get_active_write_mode
        _execute_backend_batch = ebb
        _movie_href_lookup_values = movie_href_lookup_values
        _javdb_absolute_url = javdb_absolute_url
        _absolutize_supporting_actors_json = absolutize_supporting_actors_json
        _has_meaningful_actor_data = hmad
        _batch_update_movie_actors_repo = buam
        _generate_integer_id = gii


# Constants
_PENDING_KINDS = {'movie', 'torrent'}
_KIND_MOVIE = 'movie'
_KIND_TORRENT = 'torrent'

# ── Per-href mutex for commit workflow ─────────────────────────────────────

_PENDING_HREF_LOCKS_LOCK = threading.Lock()
_PENDING_HREF_LOCKS: "dict[str, threading.Lock]" = {}


def _href_lock(href: str) -> threading.Lock:
    """Return a process-local lock for *href*.

    Phase 2 runs spider / detail / qb_uploader / pikpak_bridge as separate
    processes that share a SessionId; the per-process lock here protects
    the in-process commit loop from accidentally running twice for the
    same Href when commit / resume race inside one CLI invocation.  The
    *cross-process* lease is the caller's job (Worker / MovieClaim
    coordinator); see Phase 1 of the plan.
    """
    with _PENDING_HREF_LOCKS_LOCK:
        lock = _PENDING_HREF_LOCKS.get(href)
        if lock is None:
            lock = threading.Lock()
            _PENDING_HREF_LOCKS[href] = lock
    return lock


# ── Allowed session statuses ───────────────────────────────────────────────

_ALLOWED_STATUSES = ("in_progress", "finalizing", "committed", "failed")


# ── Category ↔ Indicator helpers ───────────────────────────────────────────

def category_to_indicators(category: str) -> Tuple[int, int]:
    """Map category name to (SubtitleIndicator, CensorIndicator)."""
    return _category_to_indicators(category)


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


# ── Upsert + delete + indicator helpers (used by legacy audit path) ──────


def _upsert_one_history_on_conn(
    conn,
    *,
    href: str,
    video_code: str,
    magnet_links: Dict[str, str],
    size_links: Dict[str, str],
    file_count_links: Dict[str, int],
    resolution_links: Dict[str, Optional[int]],
    actor_name: Optional[str],
    actor_gender: Optional[str],
    actor_link: Optional[str],
    supporting_actors: Optional[str],
    session_id: Optional[str],
) -> None:
    """Per-row upsert body, factored out so a batch caller can reuse one
    connection across many rows without re-opening / re-committing per row.

    ``session_id`` here is the already-resolved value (not the sentinel) —
    callers must run it through :func:`_resolve_session_id` first so the
    batch wrapper does not pay that resolution cost N times.
    """
    _ensure_imports()
    base_url = cfg('BASE_URL', 'https://javdb.com')
    path_href, absolute_href = _movie_href_lookup_values(href, base_url)
    lookup_hrefs = [h for h in (path_href, absolute_href) if h]
    normalized_href = absolute_href or href
    prepared_actor_link = (
        _javdb_absolute_url(actor_link, base_url) if actor_link is not None else None
    )
    prepared_supporting_actors = (
        _absolutize_supporting_actors_json(supporting_actors, base_url)
        if supporting_actors is not None
        else None
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sid = session_id
    _TORRENT_CATS = ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle')

    if len(lookup_hrefs) == 2:
        existing = conn.execute(
            "SELECT Id FROM MovieHistory WHERE Href IN (?, ?)",
            (lookup_hrefs[0], lookup_hrefs[1]),
        ).fetchone()
    elif len(lookup_hrefs) == 1:
        existing = conn.execute(
            "SELECT Id FROM MovieHistory WHERE Href = ?",
            (lookup_hrefs[0],),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT Id FROM MovieHistory WHERE Href = ?",
            (href,),
        ).fetchone()

    if existing is None:
        movie_id = _generate_integer_id()
        insert_movie = (
            """INSERT INTO MovieHistory
               (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                ActorName, ActorGender, ActorLink, SupportingActors, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (movie_id, video_code, normalized_href, now, now, now,
             actor_name, actor_gender, prepared_actor_link,
             prepared_supporting_actors, sid),
        )
        _execute_backend_batch(conn, [insert_movie])
    else:
        movie_id = existing['Id']
        if (
            actor_name is not None
            or actor_gender is not None
            or actor_link is not None
            or supporting_actors is not None
        ):
            row_m = conn.execute(
                "SELECT * FROM MovieHistory WHERE Id=?", (movie_id,),
            ).fetchone()
            new_an = (
                actor_name if actor_name is not None else row_m['ActorName']
            )
            new_ag = (
                actor_gender if actor_gender is not None else row_m['ActorGender']
            )
            new_al = (
                prepared_actor_link if actor_link is not None else row_m['ActorLink']
            )
            new_sup = (
                prepared_supporting_actors if supporting_actors is not None
                else row_m['SupportingActors']
            )
            existing_an = (row_m['ActorName'] or '').strip()
            if existing_an and not _has_meaningful_actor_data(
                new_an or '', new_al or '', new_sup or '',
            ):
                new_an = row_m['ActorName']
                new_ag = row_m['ActorGender']
                new_al = row_m['ActorLink']
                new_sup = row_m['SupportingActors']
            update_movie = (
                """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                   Href=?, ActorName=?, ActorGender=?, ActorLink=?,
                   SupportingActors=?, SessionId=? WHERE Id=?""",
                (now, now, normalized_href, new_an, new_ag, new_al, new_sup,
                 sid, movie_id),
            )
        else:
            update_movie = (
                """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                   Href=?, SessionId=? WHERE Id=?""",
                (now, now, normalized_href, sid, movie_id),
            )
        _execute_backend_batch(conn, [update_movie])

    # Upsert torrents
    has_hacked_subtitle = False
    has_subtitle = False

    for tt, magnet in magnet_links.items():
        if tt not in _TORRENT_CATS or not magnet:
            continue
        sub_ind, cen_ind = category_to_indicators(tt)
        size = size_links.get(tt, '')
        fc = file_count_links.get(tt, 0)
        res = resolution_links.get(tt)

        existing_t = conn.execute(
            """SELECT * FROM TorrentHistory
               WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?""",
            (movie_id, sub_ind, cen_ind),
        ).fetchone()

        if existing_t is None:
            torrent_id = _generate_integer_id()
            insert_torrent = (
                """INSERT INTO TorrentHistory
                   (Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                    ResolutionType, Size, FileCount, DateTimeCreated,
                    DateTimeUpdated, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (torrent_id, movie_id, magnet, sub_ind, cen_ind, res, size, fc,
                 now, now, sid),
            )
            _execute_backend_batch(conn, [insert_torrent])
        else:
            update_torrent = (
                """UPDATE TorrentHistory
                   SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                       DateTimeUpdated=?, SessionId=?
                   WHERE Id=?""",
                (magnet, size, fc, res, now, sid, existing_t['Id']),
            )
            _execute_backend_batch(conn, [update_torrent])

        if tt == 'hacked_subtitle':
            has_hacked_subtitle = True
        elif tt == 'subtitle':
            has_subtitle = True

    # If hacked_subtitle exists, remove hacked_no_subtitle
    if has_hacked_subtitle:
        _delete_torrents_with_audit(
            conn, movie_id, sub_ind=0, cen_ind=0,
            session_id=sid, when=now,
        )
    # If subtitle exists, remove no_subtitle
    if has_subtitle:
        _delete_torrents_with_audit(
            conn, movie_id, sub_ind=0, cen_ind=1,
            session_id=sid, when=now,
        )

    # Update indicators
    _update_movie_indicators(conn, movie_id, session_id=sid, when=now)


def _delete_torrents_with_audit(
    conn,
    movie_id: int,
    *,
    sub_ind: int,
    cen_ind: int,
    session_id: Optional[str],
    when: Optional[str],
) -> None:
    """Delete TorrentHistory rows matching ``(movie_id, sub_ind, cen_ind)``."""
    conn.execute(
        "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
        "AND SubtitleIndicator=? AND CensorIndicator=?",
        (movie_id, sub_ind, cen_ind),
    )


def _update_movie_indicators(
    conn,
    movie_id: int,
    *,
    session_id: Optional[str] = None,
    when: Optional[str] = None,
):
    """Recompute PerfectMatchIndicator and HiResIndicator for a movie."""
    perfect = conn.execute("""
        SELECT 1 FROM TorrentHistory t1
        JOIN TorrentHistory t2 ON t1.MovieHistoryId = t2.MovieHistoryId
        WHERE t1.MovieHistoryId = ?
          AND t1.SubtitleIndicator = 1 AND t1.CensorIndicator = 0
          AND t2.SubtitleIndicator = 1 AND t2.CensorIndicator = 1
    """, (movie_id,)).fetchone()

    hires = conn.execute("""
        SELECT 1 FROM TorrentHistory
        WHERE MovieHistoryId = ? AND ResolutionType >= 2560
    """, (movie_id,)).fetchone()

    perfect_val = 1 if perfect else 0
    hires_val = 1 if hires else 0

    if session_id is not None:
        conn.execute(
            """UPDATE MovieHistory SET PerfectMatchIndicator=?,
               HiResIndicator=?, SessionId=? WHERE Id=?""",
            (perfect_val, hires_val, session_id, movie_id),
        )
        return

    conn.execute(
        "UPDATE MovieHistory SET PerfectMatchIndicator=?, HiResIndicator=? WHERE Id=?",
        (perfect_val, hires_val, movie_id),
    )


# ── Batch update functions ────────────────────────────────────────────────


def db_batch_update_last_visited(
    hrefs: List[str],
    db_path: Optional[str] = None,
    session_id: Any = None,
) -> int:
    """Update DateTimeVisited for a batch of hrefs.

    When *session_id* is set (or :func:`get_active_session_id` returns a
    value), each affected MovieHistory row also gets ``SessionId=?``.

    Ingestion Perfect Rollback (Phase 2): when the active session runs
    under ``WriteMode='pending'`` the visit timestamps are staged into
    :data:`PendingMovieHistoryWrites` (a sparse "DateTimeVisited only"
    row per href) and applied to live in :func:`db_commit_session_history`.
    """
    _ensure_imports()
    from javdb.storage.db.db_session import _SESSION_ID_SENTINEL
    if session_id is None:
        session_id = _SESSION_ID_SENTINEL
    if not hrefs:
        return 0
    sid = _resolve_session_id(session_id)
    if sid is not None and _get_active_write_mode() == 'pending':
        # Pending route: dedupe + stage one sparse pending movie row
        # per href.  ``_pending_movie_overlay`` will sparse-merge this
        # with any earlier stages from the same session at commit time
        # so we never clobber the actor fields with the visit row's
        # NULLs.
        unique_hrefs = list(dict.fromkeys(h for h in hrefs if h))
        if not unique_hrefs:
            return 0
        for href in unique_hrefs:
            db_stage_history_write(
                sid,
                _KIND_MOVIE,
                {
                    "Href": href,
                    "DateTimeVisited": (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ),
                },
                db_path=db_path,
            )
        return len(unique_hrefs)
    base_url = cfg('BASE_URL', 'https://javdb.com')
    lookup_hrefs: List[str] = []
    for href in hrefs:
        path_href, abs_href = _movie_href_lookup_values(href, base_url)
        if path_href:
            lookup_hrefs.append(path_href)
        if abs_href:
            lookup_hrefs.append(abs_href)
        if not path_href and not abs_href and href:
            lookup_hrefs.append(href)
    lookup_hrefs = list(dict.fromkeys(lookup_hrefs))
    if not lookup_hrefs:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    CHUNK = 90
    total = 0
    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        for i in range(0, len(lookup_hrefs), CHUNK):
            chunk = lookup_hrefs[i:i + CHUNK]
            placeholders = ','.join('?' for _ in chunk)
            if sid is not None:
                cur = conn.execute(
                    f"UPDATE MovieHistory SET DateTimeVisited=?, SessionId=? "
                    f"WHERE Href IN ({placeholders})",
                    tuple([now, sid] + chunk),
                )
            else:
                cur = conn.execute(
                    f"UPDATE MovieHistory SET DateTimeVisited=? WHERE Href IN ({placeholders})",
                    [now] + chunk,
                )
            total += cur.rowcount or 0
        return total


def db_batch_update_movie_actors(
    updates: List[Tuple[str, str, str, str, str]],
    db_path: Optional[str] = None,
    session_id: Any = None,
) -> int:
    """Set actor columns and DateTimeUpdated for each
    ``(href, actor_name, actor_gender, actor_link, supporting_actors)``.

    Returns the number of rows matched by UPDATE (may be 0 for unknown hrefs).

    When *session_id* is set (or :func:`get_active_session_id` returns a
    value), each affected MovieHistory row also gets ``SessionId=?``.

    Ingestion Perfect Rollback (Phase 2): pending-mode sessions stage a
    sparse "actor fields only" pending movie row per href instead of
    UPDATE-ing live; commit merges with the earlier stages from the same
    session.
    """
    _ensure_imports()
    from javdb.storage.db.db_session import _SESSION_ID_SENTINEL
    if session_id is None:
        session_id = _SESSION_ID_SENTINEL
    if not updates:
        return 0
    sid = _resolve_session_id(session_id)
    if sid is not None and _get_active_write_mode() == 'pending':
        for href, actor_name, actor_gender, actor_link, supporting_actors in (
            updates
        ):
            if not href:
                continue
            db_stage_history_write(
                sid,
                _KIND_MOVIE,
                {
                    "Href": href,
                    "ActorName": actor_name,
                    "ActorGender": actor_gender,
                    "ActorLink": actor_link,
                    "SupportingActors": supporting_actors,
                },
                db_path=db_path,
            )
        return len([u for u in updates if u and u[0]])
    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
        return _batch_update_movie_actors_repo(
            conn, updates,
            session_id=sid,
        )


# ── Commit workflow utility functions ─────────────────────────────────────


def _href_lookup_variants(href: str) -> List[str]:
    """Return the up-to-3 Href values to look up against ``MovieHistory``.

    Mirrors the per-href lookup that :func:`_commit_one_movie` performs
    inline; extracted so the bulk session-level commit path uses the
    same variant set. Order is preserved (path-relative, absolute,
    original) and duplicates are dropped while keeping first occurrence.
    """
    _ensure_imports()
    base_url = cfg("BASE_URL", "https://javdb.com")
    path_href, abs_href = _movie_href_lookup_values(href, base_url)
    seen: set = set()
    out: List[str] = []
    for h in (path_href, abs_href, href):
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _compute_indicators(
    torrents: Iterable[Tuple[int, int, Optional[int]]],
) -> Tuple[int, int]:
    """Return ``(PerfectMatchIndicator, HiResIndicator)`` for a torrent set.

    *torrents* is an iterable of ``(SubtitleIndicator, CensorIndicator,
    ResolutionType)`` tuples representing the projected post-write state
    for a single ``MovieHistoryId``. Pure function used by the bulk
    commit path to replace the per-href JOIN SELECT + ResolutionType
    SELECT pair (see ``_commit_one_movie`` indicator-recompute block).
    """
    keys = set()
    hires = 0
    for sub, cen, resolution in torrents:
        keys.add((int(sub), int(cen)))
        if (resolution or 0) >= 2560:
            hires = 1
    perfect = 1 if ((1, 0) in keys and (1, 1) in keys) else 0
    return perfect, hires


def _bulk_run(conn, statements):
    """Run a list of ``(sql, params)`` via ``batch_execute`` if available.

    Falls back to a per-statement ``execute()`` loop when *conn* does not
    expose ``batch_execute`` (raw SQLite, used in tests). Under
    :class:`DualConnection` the call wraps :meth:`D1Connection.batch_execute`
    which auto-chunks at ``D1_BATCH_LIMIT`` (default 50) per HTTP round-trip
    while preserving submission order in the returned cursor list.
    """
    if not statements:
        return []
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        return batch(list(statements))
    return [conn.execute(sql, params) for sql, params in statements]


def _chunked(seq, size: int):
    """Yield successive *size*-length slices of *seq* as lists."""
    items = list(seq)
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ── Pending overlay wrappers ──────────────────────────────────────────────


def _merge_movie_overlay_rows(rows: Iterable[Any]) -> Dict[str, dict]:
    """Merge pending-movie rows (Seq-ascending order) into a sparse overlay."""
    from javdb.storage.db.db_history_read import (
        _merge_movie_overlay_rows as _f,
    )
    return _f(rows)


def _pending_movie_overlay(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[str, dict]:
    """Return ``{href: merged_pending_movie_row}`` for *session_id*."""
    from javdb.storage.db.db_history_read import (
        _pending_movie_overlay_impl,
    )
    return _pending_movie_overlay_impl(
        conn, session_id, href=href, include_states=include_states,
    )


def _merge_torrent_overlay_rows(
    rows: Iterable[Any],
) -> Dict[Tuple[str, int, int], dict]:
    """Merge pending-torrent rows (Seq-ascending) into a sparse overlay."""
    from javdb.storage.db.db_history_read import (
        _merge_torrent_overlay_rows as _f,
    )
    return _f(rows)


def _pending_torrent_overlay(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[Tuple[str, int, int], dict]:
    """Return ``{(href, sub, cen): merged_pending_torrent_row}`` for *session_id*."""
    from javdb.storage.db.db_history_read import (
        _pending_torrent_overlay_impl,
    )
    return _pending_torrent_overlay_impl(
        conn, session_id, href=href, include_states=include_states,
    )


# ── Per-href commit ──────────────────────────────────────────────────────


def _commit_one_movie(
    conn,
    session_id: str,
    href: str,
    *,
    when: str,
) -> Dict[str, int]:
    """Apply one Href's pending writes onto the live tables.

    The function is idempotent: it always recomputes the live row from
    ``MovieHistory + TorrentHistory + (every pending row for this href in
    this session)``, then upserts the result and marks every consumed
    pending row ``ApplyState='applied'``.  A crash + resume re-runs it
    against the same inputs and lands the same outputs.
    """
    _ensure_imports()
    counts = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
    }
    movie_overlay = _pending_movie_overlay(
        conn, session_id, href=href, include_states=("pending", "applied"),
    )
    torrent_overlay = _pending_torrent_overlay(
        conn, session_id, href=href, include_states=("pending", "applied"),
    )
    movie_payload = movie_overlay.get(href)

    base_url = cfg("BASE_URL", "https://javdb.com")
    path_href, abs_href = _movie_href_lookup_values(href, base_url)
    lookup_hrefs = [h for h in (path_href, abs_href, href) if h]

    placeholders = ",".join("?" for _ in lookup_hrefs)
    existing = conn.execute(
        f"SELECT * FROM MovieHistory WHERE Href IN ({placeholders})",
        lookup_hrefs,
    ).fetchone()

    if movie_payload is None and existing is None and not torrent_overlay:
        return counts

    if existing is None:
        video_code = (
            (movie_payload or {}).get("VideoCode")
            or next(
                (
                    r.get("VideoCode") for r in torrent_overlay.values()
                    if r.get("VideoCode")
                ),
                "",
            )
            or ""
        )
        normalized_href = abs_href or href
        movie_id = _generate_integer_id()
        conn.execute(
            """INSERT INTO MovieHistory
               (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated,
                DateTimeVisited, ActorName, ActorGender, ActorLink,
                SupportingActors, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                movie_id,
                video_code,
                normalized_href,
                when,
                when,
                (movie_payload or {}).get("DateTimeVisited") or when,
                (movie_payload or {}).get("ActorName"),
                (movie_payload or {}).get("ActorGender"),
                (movie_payload or {}).get("ActorLink"),
                (movie_payload or {}).get("SupportingActors"),
                session_id,
            ),
        )
        counts["movies_upserted"] += 1
    else:
        movie_id = int(existing["Id"])
        update_fields = ["DateTimeUpdated=?", "SessionId=?"]
        params: list = [when, session_id]
        if movie_payload is not None:
            update_fields.append("DateTimeVisited=?")
            params.append(
                movie_payload.get("DateTimeVisited") or when
            )
            for column, payload_key in (
                ("ActorName", "ActorName"),
                ("ActorGender", "ActorGender"),
                ("ActorLink", "ActorLink"),
                ("SupportingActors", "SupportingActors"),
                ("VideoCode", "VideoCode"),
            ):
                value = movie_payload.get(payload_key)
                if value is not None:
                    update_fields.append(f"{column}=?")
                    params.append(value)
        params.append(movie_id)
        conn.execute(
            f"UPDATE MovieHistory SET {', '.join(update_fields)} WHERE Id=?",
            params,
        )
        counts["movies_upserted"] += 1

    consumed_movie_seqs: list = []
    for r in movie_overlay.values():
        # ``_merged_seqs`` is populated by ``_pending_movie_overlay``
        # for sparse-merge mode (Phase 2: visit-only / actor-only
        # stages contribute multiple rows per href).  Fall back to
        # ``Seq`` for the legacy single-row case to stay defensive.
        seqs = r.get("_merged_seqs") or [r["Seq"]]
        consumed_movie_seqs.extend(seqs)

    consumed_torrent_seqs: list = []
    for (_, sub, cen), payload in torrent_overlay.items():
        existing_t = conn.execute(
            "SELECT Id FROM TorrentHistory "
            "WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?",
            (movie_id, int(sub), int(cen)),
        ).fetchone()
        if existing_t is None:
            conn.execute(
                """INSERT INTO TorrentHistory
                   (Id, MovieHistoryId, MagnetUri, SubtitleIndicator,
                    CensorIndicator, ResolutionType, Size, FileCount,
                    DateTimeCreated, DateTimeUpdated, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _generate_integer_id(),
                    movie_id,
                    payload.get("MagnetUri"),
                    int(sub),
                    int(cen),
                    payload.get("ResolutionType"),
                    payload.get("Size") or "",
                    int(payload.get("FileCount") or 0),
                    when,
                    when,
                    session_id,
                ),
            )
        else:
            conn.execute(
                """UPDATE TorrentHistory
                   SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                       DateTimeUpdated=?, SessionId=?
                   WHERE Id=?""",
                (
                    payload.get("MagnetUri"),
                    payload.get("Size") or "",
                    int(payload.get("FileCount") or 0),
                    payload.get("ResolutionType"),
                    when,
                    session_id,
                    int(existing_t["Id"]),
                ),
            )
        counts["torrents_upserted"] += 1
        # P0-4: consume EVERY pending row that fed into this merged
        # payload, not just the last Seq. ``_pending_torrent_overlay``
        # now populates ``_merged_seqs`` for the same reason
        # ``_pending_movie_overlay`` does — re-staging (retry / re-fetch
        # / sparse-merge) creates multiple rows per (href, sub, cen)
        # and the legacy single-Seq update silently left the earlier
        # rows stuck in ``ApplyState='pending'``, which then tripped
        # the Phase 3 residual-pending alert.
        merged = payload.get("_merged_seqs")
        if merged:
            consumed_torrent_seqs.extend(merged)
        else:
            consumed_torrent_seqs.append(payload["Seq"])

    # Apply the same "hacked_subtitle wins over hacked_no_subtitle, subtitle
    # wins over no_subtitle" rule enforced in _upsert_one_history_on_conn.
    has_hacked_sub = any(
        sub == 1 and cen == 0 for (_, sub, cen) in torrent_overlay.keys()
    )
    has_subtitle = any(
        sub == 1 and cen == 1 for (_, sub, cen) in torrent_overlay.keys()
    )
    if has_hacked_sub:
        cur = conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=0 AND CensorIndicator=0",
            (movie_id,),
        )
        counts["torrents_deleted"] += cur.rowcount or 0
    if has_subtitle:
        cur = conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=0 AND CensorIndicator=1",
            (movie_id,),
        )
        counts["torrents_deleted"] += cur.rowcount or 0

    # Recompute derived indicators directly.
    perfect_row = conn.execute(
        "SELECT 1 FROM TorrentHistory t1 "
        "JOIN TorrentHistory t2 ON t1.MovieHistoryId=t2.MovieHistoryId "
        "WHERE t1.MovieHistoryId=? "
        "AND t1.SubtitleIndicator=1 AND t1.CensorIndicator=0 "
        "AND t2.SubtitleIndicator=1 AND t2.CensorIndicator=1",
        (movie_id,),
    ).fetchone()
    hires_row = conn.execute(
        "SELECT 1 FROM TorrentHistory "
        "WHERE MovieHistoryId=? AND ResolutionType >= 2560",
        (movie_id,),
    ).fetchone()
    conn.execute(
        "UPDATE MovieHistory SET PerfectMatchIndicator=?, "
        "HiResIndicator=? WHERE Id=?",
        (1 if perfect_row else 0, 1 if hires_row else 0, movie_id),
    )

    consumed_seqs = consumed_movie_seqs
    if consumed_seqs:
        ph = ",".join("?" for _ in consumed_seqs)
        cur = conn.execute(
            f"UPDATE PendingMovieHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            consumed_seqs,
        )
        counts["pending_marked_applied"] += cur.rowcount or 0
    if consumed_torrent_seqs:
        ph = ",".join("?" for _ in consumed_torrent_seqs)
        cur = conn.execute(
            f"UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            consumed_torrent_seqs,
        )
        counts["pending_marked_applied"] += cur.rowcount or 0
    return counts


# ── Bulk session commit ──────────────────────────────────────────────────


def _commit_session_bulk(
    conn,
    session_id: str,
    *,
    when: str,
    exclude_movie_seqs: Optional[set] = None,
    exclude_torrent_seqs: Optional[set] = None,
) -> Tuple[Dict[str, int], set, set]:
    """Session-level bulk variant of the per-href :func:`_commit_one_movie`.

    Semantically equivalent to applying :func:`_commit_one_movie` to every
    pending href in the session, but collapses ~13–20 D1 round-trips per
    href into O(N/50 + const) batched HTTP requests. See
    ``.claude/plans/apps-cli-commit-session-ingestion-spide-gentle-sloth.md``.

    Returns ``(counts, consumed_movie_seqs, consumed_torrent_seqs)``. The
    Seq sets let the drain wrapper exclude already-processed rows on the
    next pass without an extra ``NOT IN`` round-trip (we filter in Python
    after the SELECT to stay under D1's 100-param-per-statement cap).
    """
    _ensure_imports()
    exclude_movie_seqs = exclude_movie_seqs or set()
    exclude_torrent_seqs = exclude_torrent_seqs or set()
    counts: Dict[str, int] = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
    }

    # ── Phase A: bulk prefetch (2 SELECTs over Pending* tables) ─────────
    overlay_cursors = _bulk_run(conn, [
        (
            "SELECT * FROM PendingMovieHistoryWrites "
            "WHERE SessionId=? AND ApplyState IN ('pending','applied') "
            "ORDER BY Seq ASC",
            (session_id,),
        ),
        (
            "SELECT * FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState IN ('pending','applied') "
            "ORDER BY Seq ASC",
            (session_id,),
        ),
    ])
    raw_movie_rows = [
        r for r in overlay_cursors[0].fetchall()
        if r["Seq"] not in exclude_movie_seqs
    ]
    raw_torrent_rows = [
        r for r in overlay_cursors[1].fetchall()
        if r["Seq"] not in exclude_torrent_seqs
    ]
    movie_overlay = _merge_movie_overlay_rows(raw_movie_rows)
    torrent_overlay = _merge_torrent_overlay_rows(raw_torrent_rows)

    if not movie_overlay and not torrent_overlay:
        return counts, set(), set()

    hrefs = sorted(
        set(movie_overlay.keys()) | {k[0] for k in torrent_overlay.keys()}
    )
    href_to_variants = {h: _href_lookup_variants(h) for h in hrefs}
    variant_to_href: Dict[str, str] = {}
    for h, variants in href_to_variants.items():
        for v in variants:
            variant_to_href.setdefault(v, h)

    # ── Phase A.2: bulk-lookup existing MovieHistory by Href variants ──
    # Chunk by 99 params (under D1's 100-param-per-statement cap).
    live_movies_by_href: Dict[str, dict] = {}
    movie_lookup_stmts = []
    all_variants = list(variant_to_href.keys())
    for chunk in _chunked(all_variants, 99):
        ph = ",".join("?" for _ in chunk)
        movie_lookup_stmts.append((
            f"SELECT * FROM MovieHistory WHERE Href IN ({ph})",
            tuple(chunk),
        ))
    for cur in _bulk_run(conn, movie_lookup_stmts):
        for row in cur.fetchall():
            d = dict(row)
            canonical = variant_to_href.get(d["Href"])
            if canonical and canonical not in live_movies_by_href:
                live_movies_by_href[canonical] = d

    # Build torrent-overlay grouped by canonical href.
    torrents_by_href: Dict[str, Dict[Tuple[int, int], dict]] = {}
    for (h, sub, cen), payload in torrent_overlay.items():
        torrents_by_href.setdefault(h, {})[(int(sub), int(cen))] = payload

    # ── Phase B: classify each href into INSERT-new / UPDATE / skip ──
    base_url = cfg("BASE_URL", "https://javdb.com")
    # href → (sql, params) for new-movie INSERTs.  Ids are pre-generated so
    # we never need cur.lastrowid (which is unreliable across dual-write
    # backends under STORAGE_BACKEND=dual — see C.1 in the audit plan).
    new_movie_insert_stmts: List[Tuple[str, tuple]] = []
    movie_updates: List[Tuple[str, tuple]] = []
    consumed_movie_seqs: List[str] = []
    consumed_torrent_seqs: List[str] = []

    href_to_movie_id: Dict[str, int] = {
        h: int(row["Id"]) for h, row in live_movies_by_href.items()
    }

    for href in hrefs:
        movie_payload = movie_overlay.get(href)
        existing = live_movies_by_href.get(href)
        torrents_here = torrents_by_href.get(href, {})

        if movie_payload is None and existing is None and not torrents_here:
            continue

        if movie_payload is not None:
            seqs = movie_payload.get("_merged_seqs") or [movie_payload["Seq"]]
            consumed_movie_seqs.extend(seqs)

        if existing is None:
            video_code = (
                (movie_payload or {}).get("VideoCode")
                or next(
                    (
                        r.get("VideoCode") for r in torrents_here.values()
                        if r.get("VideoCode")
                    ),
                    "",
                )
                or ""
            )
            _, abs_href = _movie_href_lookup_values(href, base_url)
            normalized_href = abs_href or href
            movie_id = _generate_integer_id()
            href_to_movie_id[href] = movie_id
            new_movie_insert_stmts.append((
                """INSERT INTO MovieHistory
                   (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated,
                    DateTimeVisited, ActorName, ActorGender, ActorLink,
                    SupportingActors, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    movie_id,
                    video_code,
                    normalized_href,
                    when,
                    when,
                    (movie_payload or {}).get("DateTimeVisited") or when,
                    (movie_payload or {}).get("ActorName"),
                    (movie_payload or {}).get("ActorGender"),
                    (movie_payload or {}).get("ActorLink"),
                    (movie_payload or {}).get("SupportingActors"),
                    session_id,
                ),
            ))
        else:
            movie_id = int(existing["Id"])
            update_fields = ["DateTimeUpdated=?", "SessionId=?"]
            up_params: list = [when, session_id]
            if movie_payload is not None:
                update_fields.append("DateTimeVisited=?")
                up_params.append(movie_payload.get("DateTimeVisited") or when)
                for column, payload_key in (
                    ("ActorName", "ActorName"),
                    ("ActorGender", "ActorGender"),
                    ("ActorLink", "ActorLink"),
                    ("SupportingActors", "SupportingActors"),
                    ("VideoCode", "VideoCode"),
                ):
                    value = movie_payload.get(payload_key)
                    if value is not None:
                        update_fields.append(f"{column}=?")
                        up_params.append(value)
            up_params.append(movie_id)
            movie_updates.append((
                f"UPDATE MovieHistory SET {', '.join(update_fields)} WHERE Id=?",
                tuple(up_params),
            ))
        counts["movies_upserted"] += 1

    # ── Phase C1: flush new-movie INSERTs in batches ─────────────────────
    # Ids are already in href_to_movie_id; no lastrowid needed.
    for chunk in _chunked(new_movie_insert_stmts, 50):
        _bulk_run(conn, chunk)

    # ── Phase C2: bulk-read live TorrentHistory by MovieHistoryId ───────
    live_torrents_by_mid: Dict[int, Dict[Tuple[int, int], dict]] = {}
    torrent_lookup_stmts = []
    for chunk in _chunked(href_to_movie_id.values(), 100):
        ph = ",".join("?" for _ in chunk)
        torrent_lookup_stmts.append((
            f"SELECT * FROM TorrentHistory WHERE MovieHistoryId IN ({ph})",
            tuple(chunk),
        ))
    for cur in _bulk_run(conn, torrent_lookup_stmts):
        for row in cur.fetchall():
            d = dict(row)
            mid = int(d["MovieHistoryId"])
            live_torrents_by_mid.setdefault(mid, {})[
                (int(d["SubtitleIndicator"]), int(d["CensorIndicator"]))
            ] = d

    # ── Phase D: torrent writes + queued movie UPDATEs ──────────────────
    write_stmts: List[Tuple[str, tuple]] = list(movie_updates)
    # Projected post-write torrent state — used by Phase E indicator recompute.
    projected: Dict[int, Dict[Tuple[int, int], Optional[int]]] = {}
    for mid, live in live_torrents_by_mid.items():
        projected[mid] = {
            (s, c): row.get("ResolutionType") for (s, c), row in live.items()
        }

    for href in hrefs:
        mid = href_to_movie_id.get(href)
        if mid is None:
            continue
        torrents_here = torrents_by_href.get(href, {})
        live_for_movie = live_torrents_by_mid.get(mid, {})
        proj = projected.setdefault(mid, {})

        for (sub, cen), payload in torrents_here.items():
            sub_i, cen_i = int(sub), int(cen)
            resolution = payload.get("ResolutionType")
            existing_t = live_for_movie.get((sub_i, cen_i))
            if existing_t is None:
                write_stmts.append((
                    """INSERT INTO TorrentHistory
                       (Id, MovieHistoryId, MagnetUri, SubtitleIndicator,
                        CensorIndicator, ResolutionType, Size, FileCount,
                        DateTimeCreated, DateTimeUpdated, SessionId)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _generate_integer_id(),
                        mid,
                        payload.get("MagnetUri"),
                        sub_i,
                        cen_i,
                        resolution,
                        payload.get("Size") or "",
                        int(payload.get("FileCount") or 0),
                        when,
                        when,
                        session_id,
                    ),
                ))
            else:
                write_stmts.append((
                    """UPDATE TorrentHistory
                       SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                           DateTimeUpdated=?, SessionId=?
                       WHERE Id=?""",
                    (
                        payload.get("MagnetUri"),
                        payload.get("Size") or "",
                        int(payload.get("FileCount") or 0),
                        resolution,
                        when,
                        session_id,
                        int(existing_t["Id"]),
                    ),
                ))
            proj[(sub_i, cen_i)] = resolution
            counts["torrents_upserted"] += 1
            merged = payload.get("_merged_seqs")
            if merged:
                consumed_torrent_seqs.extend(merged)
            else:
                consumed_torrent_seqs.append(payload["Seq"])

        # Conflict-deletion rules (mirror _commit_one_movie):
        #   hacked_subtitle (1,0) shadows no_subtitle (0,0)
        #   subtitle        (1,1) shadows no_subtitle_cen (0,1)
        has_hacked_sub = any(k == (1, 0) for k in torrents_here.keys())
        has_subtitle = any(k == (1, 1) for k in torrents_here.keys())
        if has_hacked_sub:
            write_stmts.append((
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=0",
                (mid,),
            ))
            if (0, 0) in proj:
                del proj[(0, 0)]
                counts["torrents_deleted"] += 1
        if has_subtitle:
            write_stmts.append((
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=1",
                (mid,),
            ))
            if (0, 1) in proj:
                del proj[(0, 1)]
                counts["torrents_deleted"] += 1

    for chunk in _chunked(write_stmts, 50):
        _bulk_run(conn, chunk)

    # ── Phase E: indicator recompute in memory ──────────────────────────
    indicator_updates: List[Tuple[str, tuple]] = []
    for mid in sorted(set(href_to_movie_id.values())):
        proj = projected.get(mid, {})
        perfect, hires = _compute_indicators(
            (s, c, r) for (s, c), r in proj.items()
        )
        indicator_updates.append((
            "UPDATE MovieHistory SET PerfectMatchIndicator=?, "
            "HiResIndicator=? WHERE Id=?",
            (perfect, hires, mid),
        ))

    # ── Phase F: indicator UPDATEs + apply-mark UPDATEs ─────────────────
    apply_mark_stmts: List[Tuple[str, tuple]] = []
    for chunk in _chunked(consumed_movie_seqs, 99):
        ph = ",".join("?" for _ in chunk)
        apply_mark_stmts.append((
            f"UPDATE PendingMovieHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            tuple(chunk),
        ))
    for chunk in _chunked(consumed_torrent_seqs, 99):
        ph = ",".join("?" for _ in chunk)
        apply_mark_stmts.append((
            f"UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            tuple(chunk),
        ))

    final_stmts = indicator_updates + apply_mark_stmts
    for chunk in _chunked(final_stmts, 50):
        cursors = _bulk_run(conn, chunk)
        for (sql, _p), cur in zip(chunk, cursors):
            if sql.startswith("UPDATE PendingMovieHistoryWrites") \
                    or sql.startswith("UPDATE PendingTorrentHistoryWrites"):
                counts["pending_marked_applied"] += int(
                    getattr(cur, "rowcount", 0) or 0
                )

    return counts, set(consumed_movie_seqs), set(consumed_torrent_seqs)


# ── Query helpers ─────────────────────────────────────────────────────────


def _pending_distinct_hrefs(conn, session_id: str) -> List[str]:
    """Return every Href that has at least one pending row for *session_id*."""
    rows = conn.execute(
        "SELECT Href FROM ("
        "  SELECT Href FROM PendingMovieHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied') "
        "  UNION "
        "  SELECT Href FROM PendingTorrentHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied')"
        ") ORDER BY Href",
        (session_id, session_id),
    ).fetchall()
    return [r["Href"] for r in rows]


def _d1_retry_pending_cleanup(session_id: str) -> None:
    """Best-effort D1-direct retry for pending-row cleanup.

    After the normal DualConnection commit flow, any D1-side failures on
    the ApplyState UPDATE or the final DELETE leave orphaned 'pending'
    rows in D1. Since the session is already committed and the live
    tables are consistent, we can safely mark remaining pending rows as
    applied and delete them directly on D1.
    """
    from javdb.storage.db.db_connection import current_backend
    if current_backend() not in ('d1', 'dual'):
        return
    try:
        from javdb.storage.d1_client import make_d1_connection
    except Exception:
        return
    d1 = None
    try:
        d1 = make_d1_connection('history')
        for table in ('PendingMovieHistoryWrites', 'PendingTorrentHistoryWrites'):
            d1.execute(
                f"UPDATE {table} SET ApplyState='applied' "
                f"WHERE SessionId=? AND ApplyState='pending'",
                (session_id,),
            )
            d1.execute(
                f"DELETE FROM {table} "
                f"WHERE SessionId=? AND ApplyState='applied'",
                (session_id,),
            )
    except Exception as exc:
        logger.warning(
            "D1 retry pending cleanup failed for session %s: %s",
            session_id, exc,
        )
    finally:
        if d1 is not None:
            try:
                d1.close()
            except Exception:
                pass


# ── Main commit entry point ──────────────────────────────────────────────


def db_commit_session_history(
    session_id: str,
    *,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Drain pending writes for *session_id* into MovieHistory / TorrentHistory.

    State transitions executed:

      in_progress -> finalizing  (set up-front)
      finalizing -> committed    (set when every Href has applied)

    Returns aggregate per-table counts.  Callers should treat the
    function as the canonical "drain pending" entry point; recovery
    from a crash midway through is via :func:`db_resume_finalizing_session`.
    """
    _ensure_imports()
    from javdb.storage.db.db_reports import (
        db_get_session_status,
        db_begin_finalize_session,
        db_finish_commit_session,
    )

    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    counts = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
        "pending_deleted": 0,
        "hrefs_processed": 0,
    }

    state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    if state is None:
        return counts
    write_mode, status = state
    if write_mode != "pending":
        raise ValueError(
            f"db_commit_session_history: session {session_id} has "
            f"WriteMode={write_mode!r}; expected 'pending'"
        )
    if status not in ("in_progress", "finalizing", "committed"):
        raise ValueError(
            f"db_commit_session_history: session {session_id} has "
            f"Status={status!r}; expected one of in_progress / "
            f"finalizing / committed"
        )

    if status == "in_progress":
        db_begin_finalize_session(session_id, db_path=reports_db_path)

    use_bulk = os.getenv("COMMIT_SESSION_BULK", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )

    if use_bulk:
        # Bulk path: collapse the per-href loop into 2 SELECTs + chunked
        # batched writes per drain pass. See plan at
        # .claude/plans/apps-cli-commit-session-ingestion-spide-gentle-sloth.md
        # Drain across up to 4 passes (1 initial + 3 rescans) so that pending
        # rows staged AFTER our prefetch by a concurrent stager are still
        # absorbed — Seqs are excluded post-SELECT (no NOT IN, under D1's
        # 100-param cap).
        seen_movie: set = set()
        seen_torrent: set = set()
        hrefs_seen: set = set()
        for attempt in range(4):
            with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
                pass_counts, new_m, new_t = _commit_session_bulk(
                    conn, session_id, when=when,
                    exclude_movie_seqs=seen_movie,
                    exclude_torrent_seqs=seen_torrent,
                )
                # Capture which hrefs were touched in this pass via the
                # consumed Seq sets — we re-derive hrefs in the bulk
                # function so this is a cheap follow-up SELECT only when
                # we need a final hrefs_processed count.
            if not new_m and not new_t:
                break
            for k, v in pass_counts.items():
                counts[k] = counts.get(k, 0) + v
            seen_movie |= new_m
            seen_torrent |= new_t
            if attempt >= 1:
                logger.info(
                    "db_commit_session_history(session=%s, bulk=1): "
                    "rescan pass %d absorbed %d movie + %d torrent Seq(s)",
                    session_id, attempt, len(new_m), len(new_t),
                )
        # hrefs_processed: count distinct hrefs touched. Re-derive from the
        # union of consumed Seqs by sampling Pending tables one more time.
        # Cheap (single SELECT) and stays consistent with the audit path.
        with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
            counts["hrefs_processed"] = len(
                _pending_distinct_hrefs(conn, session_id)
            )
    else:
        # P1: snapshot the href list, but re-scan at the end so any pending
        # rows staged AFTER the initial scan (by a concurrent stager that
        # raced this finalize) are not left stuck in ``ApplyState='pending'``
        # — that residue is the Phase 3 critical alert trigger.
        processed: set = set()
        with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
            hrefs = _pending_distinct_hrefs(conn, session_id)

        def _drain(href_list):
            for href in href_list:
                if href in processed:
                    continue
                with _href_lock(href):
                    with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
                        per_movie = _commit_one_movie(
                            conn, session_id, href, when=when,
                        )
                        for k, v in per_movie.items():
                            counts[k] = counts.get(k, 0) + v
                processed.add(href)

        _drain(hrefs)

        # Re-scan for hrefs that arrived after the initial snapshot. Bounded
        # by a small loop count to avoid the (pathological) case where a
        # stager keeps adding pending rows in lock-step with this finalize.
        for _ in range(3):
            with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
                extra = [h for h in _pending_distinct_hrefs(conn, session_id)
                         if h not in processed]
            if not extra:
                break
            logger.info(
                "db_commit_session_history(session=%s): rescan found %d "
                "additional pending href(s) staged after initial snapshot",
                session_id, len(extra),
            )
            _drain(extra)

        counts["hrefs_processed"] = len(processed)

    # Flip Status to 'committed' BEFORE the final pending-table DELETE so a
    # crash between the two leaves a recoverable footprint.  Failure modes:
    #   * crash before flip → Status='finalizing' + applied rows.  Resume
    #     re-runs the loop (idempotent per ``_commit_one_movie`` docstring),
    #     reaches this point, flips, deletes.
    #   * crash after flip, before delete → Status='committed' + applied
    #     rows.  Resume re-enters via ``db_resume_finalizing_session`` which
    #     accepts 'committed', re-runs the loop (idempotent on already-
    #     applied rows since ``_pending_*_overlay`` reads both states),
    #     reaches the no-op flip, deletes.
    # The reverse order (delete first, flip last) was monitoring-hostile:
    # a crash mid-flip left ``Status='finalizing'`` with zero pending rows,
    # which any "stuck session" alert misreads as a hung commit.
    db_finish_commit_session(session_id, db_path=reports_db_path)

    with _get_db(history_db_path or _HISTORY_DB_PATH) as conn:
        cur_m = conn.execute(
            "DELETE FROM PendingMovieHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (session_id,),
        )
        cur_t = conn.execute(
            "DELETE FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (session_id,),
        )
        counts["pending_deleted"] = (cur_m.rowcount or 0) + (cur_t.rowcount or 0)

    _d1_retry_pending_cleanup(session_id)

    return counts


def db_resume_finalizing_session(
    session_id: str,
    *,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Idempotently finish a session left in ``Status='finalizing'``.

    Identical to :func:`db_commit_session_history` aside from the
    pre-condition: the session must already be in ``finalizing`` (or
    ``committed`` — then the call is a no-op).  Used by the rollback CLI
    to drive a crashed-mid-commit session to ``committed`` instead of
    rewinding it.
    """
    _ensure_imports()
    from javdb.storage.db.db_reports import db_get_session_status

    state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    if state is None:
        return {
            "movies_upserted": 0,
            "torrents_upserted": 0,
            "torrents_deleted": 0,
            "pending_marked_applied": 0,
            "pending_deleted": 0,
            "hrefs_processed": 0,
        }
    write_mode, status = state
    if write_mode != "pending":
        raise ValueError(
            f"db_resume_finalizing_session: session {session_id} has "
            f"WriteMode={write_mode!r}; expected 'pending'"
        )
    if status not in ("finalizing", "committed"):
        raise ValueError(
            f"db_resume_finalizing_session: session {session_id} has "
            f"Status={status!r}; expected 'finalizing' or 'committed'"
        )
    return db_commit_session_history(
        session_id,
        history_db_path=history_db_path,
        reports_db_path=reports_db_path,
    )
