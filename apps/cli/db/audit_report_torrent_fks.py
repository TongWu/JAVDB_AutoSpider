"""Audit (and optionally repair) ``ReportTorrents.ReportMovieId`` damage.

Historical-damage assessment tool for BFR-034
(``docs/design/BFR-034-Sqlite-Lastrowid-As-D1-Foreign-Key/``).
Before commit ``cd507c88``, ``db_insert_report_rows`` read the new
``ReportMovies.Id`` back from ``cur.lastrowid``. Under
``STORAGE_BACKEND=dual`` that is the *SQLite* rowid while D1 allocates its
own AUTOINCREMENT id, so once the counters diverged every D1-side
``ReportTorrents`` row attached to a **different session's** movie. The FK
stayed satisfied (the stale id usually exists), nothing raised, and the
rows escape ``apps.cli.db.rollback`` /
``cleanup_orphaned_session_rows`` because both resolve children via
``ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)``.

This tool finds that damage on whatever backend ``STORAGE_BACKEND``
resolves to (ops run it with the ``d1`` env, since D1 is canonical).

Detection signals
-----------------
The reports schema is the constraint. ``ReportTorrents`` carries
``(Id, ReportMovieId, VideoCode, MagnetUri, SubtitleIndicator,
CensorIndicator, ResolutionType, Size, FileCount)`` — **no ``SessionId``
and no timestamp** — so a torrent's session can only be reached *through*
the FK under audit, and no time-window heuristic is possible. What is
available is ``VideoCode``, which every writer
(``_db_reports.db_insert_report_rows``,
``_db_migrations`` step 7, ``migrations/tools/csv_to_sqlite.py``) copies
from the *same* source row as the parent ``ReportMovies.VideoCode``.

``ORPHANED_TORRENT`` (deterministic)
    ``ReportMovieId`` resolves to no ``ReportMovies`` row. Certain damage:
    a dangling FK. The parent — and therefore the correct session — is
    gone, so the only safe repair is deletion.

``VIDEO_CODE_MISMATCH`` (deterministic)
    The referenced movie exists but its ``VideoCode`` differs from the
    torrent's. Since both come from one source row, a mismatch is
    definitively wrong attribution. Deterministic as *detection* only —
    it proves the current parent is wrong, never which parent is right —
    so the re-attachment is report-only (see Repair below).

``UNEXPLAINED_DUPLICATE_SLOT`` (heuristic)
    Every writer emits at most one torrent per
    ``(SubtitleIndicator, CensorIndicator)`` slot per movie, so two rows
    in one slot under one movie means foreign children landed there. This
    is the residual signal for damage the business key cannot see — a
    mis-pointed torrent whose stale id happened to hit *another copy of
    the same video code*. Groups already accounted for by
    ``VIDEO_CODE_MISMATCH`` are subtracted. Heuristic, never repaired:
    the shape says "something extra is here", not which row is foreign.

Not implementable: a session-mismatch signal comparing
``ReportTorrents.SessionId`` to its parent's — that column does not
exist. Also inherently invisible: mis-pointed rows whose stale parent
carries the *same* ``VideoCode`` and occupies a *different* category slot
(they are indistinguishable from correct rows), and rows whose
``VideoCode`` is NULL / ``''`` on either side (counted and reported as
unverifiable, never repaired). And inherently **unprovable from this
schema**: the *intended* parent of any mis-pointed row — no ``SessionId``
and no timestamp means no evidence ties a torrent to a day, so a
candidate parent can be suggested but never confirmed in-database. The
repair gate below matches that limit.

Repair (orphan deletion only — re-attachment is report-only)
------------------------------------------------------------
**No ``VIDEO_CODE_MISMATCH`` row is ever re-attached automatically**, not
even under ``--apply --force``. An earlier revision of this tool did
re-attach when **exactly one** ``ReportMovies`` row carried the torrent's
``VideoCode`` and that candidate had neither the same ``MagnetUri`` nor
an occupied ``(SubtitleIndicator, CensorIndicator)`` slot. That is the
right *shape* — the bug leaves the true parent childless — but "currently
the only movie with this code" is not provenance, and the damage pattern
makes the wrong answer common rather than exotic: because the mis-pointed
torrent escapes session rollback while its true parent does not (see the
BFR's "escapes session rollback" section), the true parent is frequently
**already deleted**, and a later re-scrape has recreated the same
``VideoCode`` under a **new** session. That later movie then *is* the
unique candidate, and re-attaching to it writes a confidently wrong
attribution into canonical D1 — worse than leaving the row visibly broken.

So the candidate is still computed, and only *printed*: such rows are
classified ``single_candidate_unverifiable_provenance`` alongside the
other report-only reasons, and the report emits the exact ``UPDATE``
statement for an operator who has corroborated provenance out of band —
the committed, dated ``DailyReport`` CSVs in git history do persist which
videos and magnets belonged to which day. There is deliberately no flag
to force it; the printed SQL is the escape hatch.

Orphan deletion is therefore the only automated repair, and it requires
``--apply --force --delete-orphans`` because it is irreversible.

Exit codes
----------
* 0 — clean (or ``--apply --force --delete-orphans`` left the DB clean)
* 1 — **retired; never returned.** It used to mean "findings, all inside
  the auto-repairable subset". With re-attachment demoted to report-only
  no verdict maps to 1 any more. The code is left unassigned rather than
  reused, so an existing runbook reading 1 cannot mis-read a new meaning.
* 2 — findings; all of them now need a human (report-only re-attachment
  suggestions, ambiguous or unverifiable mismatches, the heuristic
  duplicate-slot signal, or orphans awaiting ``--delete-orphans``). Also
  an ``--apply`` refused by the ``--max-repairs`` safety rail.
* 3 — the reports DB could not be read

Usage
-----
    # report only (default) — never writes
    python3 -m apps.cli.db.audit_report_torrent_fks
    python3 -m apps.cli.db.audit_report_torrent_fks --json

    # the only automated repair: delete orphans (irreversible)
    python3 -m apps.cli.db.audit_report_torrent_fks --apply --force \
        --delete-orphans
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import javdb.storage.db as _db
from javdb.storage.db import current_backend, get_db
from javdb.infra.logging import get_logger, log_summary_block, setup_logging


logger = get_logger(__name__)


SIGNAL_ORPHAN = "ORPHANED_TORRENT"
SIGNAL_CODE_MISMATCH = "VIDEO_CODE_MISMATCH"
SIGNAL_DUPLICATE_SLOT = "UNEXPLAINED_DUPLICATE_SLOT"

SIGNAL_CONFIDENCE = {
    SIGNAL_ORPHAN: "deterministic",
    SIGNAL_CODE_MISMATCH: "deterministic",
    SIGNAL_DUPLICATE_SLOT: "heuristic",
}

# Why a VIDEO_CODE_MISMATCH row is left alone by --apply. Every mismatch
# gets exactly one of these: none of them is auto-repairable.
REASON_NO_CANDIDATE = "no_candidate_parent"
REASON_MULTIPLE_CANDIDATES = "multiple_candidate_parents"
REASON_MAGNET_PRESENT = "candidate_already_has_magnet"
REASON_SLOT_OCCUPIED = "candidate_slot_occupied"
# Exactly one candidate parent with a free slot — the shape the bug
# produces, and still not proof: ReportTorrents has no SessionId and no
# timestamp, and the true parent is often gone (rollback escape), so the
# lone survivor may be a *later* re-scrape of the same VideoCode. Printed
# as a suggested manual repair, never applied. See the module docstring.
REASON_SINGLE_CANDIDATE = "single_candidate_unverifiable_provenance"

# Printed with every suggestion so the JSON output carries its own caveat.
MANUAL_CORROBORATION_NOTE = (
    "requires manual corroboration of provenance (e.g. against the "
    "committed dated DailyReport CSVs in git history) before applying"
)

VERDICT_CLEAN = "CLEAN"
VERDICT_ESCALATE = "ESCALATE_MANUAL_REVIEW"

# Exit 1 is deliberately absent: it used to mean "findings, all inside the
# auto-repairable subset", and demoting single-candidate re-attachment to
# report-only left that class with no producer. Unassigned, not reused.
VERDICT_EXIT_CODE = {
    VERDICT_CLEAN: 0,
    VERDICT_ESCALATE: 2,
}

# Matches apps.cli.db.cleanup_stale_in_progress: 3 == could not read the DB.
EXIT_DB_ERROR = 3

# D1 caps bound SQL variables per statement; chunk IN-lists (same bound as
# javdb/migrations/tools/cleanup_orphaned_session_rows.py).
_CHUNK = 90


# ── row helpers (sqlite3.Row and D1 dict rows both index by name) ────────


def _dicts(rows: Iterable[Any]) -> List[dict]:
    """Normalise sqlite3.Row / D1 dict rows into plain JSON-able dicts."""
    out: List[dict] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({k: row[k] for k in row.keys()})
    return out


def _count(conn, sql: str, params: Sequence[Any] = ()) -> int:
    row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        return 0
    value = row["n"] if not isinstance(row, dict) else row.get("n")
    return int(value or 0)


def _chunks(seq: Sequence[Any], size: int = _CHUNK) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _slot(row: dict) -> Tuple[Any, Any]:
    return (row.get("SubtitleIndicator"), row.get("CensorIndicator"))


# ── signal queries ──────────────────────────────────────────────────────


_ORPHAN_WHERE = """
    FROM ReportTorrents rt
    WHERE NOT EXISTS (
        SELECT 1 FROM ReportMovies rm WHERE rm.Id = rt.ReportMovieId
    )
"""

_MISMATCH_WHERE = """
    FROM ReportTorrents rt
    JOIN ReportMovies rm ON rm.Id = rt.ReportMovieId
    WHERE COALESCE(rt.VideoCode, '') <> ''
      AND COALESCE(rm.VideoCode, '') <> ''
      AND rt.VideoCode <> rm.VideoCode
"""

_UNVERIFIABLE_WHERE = """
    FROM ReportTorrents rt
    JOIN ReportMovies rm ON rm.Id = rt.ReportMovieId
    WHERE COALESCE(rt.VideoCode, '') = ''
       OR COALESCE(rm.VideoCode, '') = ''
"""


def find_orphans(conn, limit: int) -> Tuple[int, List[dict]]:
    """Torrents whose ``ReportMovieId`` resolves to no movie row."""
    total = _count(conn, "SELECT COUNT(*) AS n" + _ORPHAN_WHERE)
    rows = _dicts(conn.execute(
        """SELECT rt.Id AS TorrentId, rt.ReportMovieId AS AttachedMovieId,
                  rt.VideoCode AS TorrentVideoCode, rt.MagnetUri AS MagnetUri,
                  rt.SubtitleIndicator AS SubtitleIndicator,
                  rt.CensorIndicator AS CensorIndicator"""
        + _ORPHAN_WHERE
        + " ORDER BY rt.Id LIMIT ?",
        (limit,),
    ).fetchall())
    return total, rows


def find_code_mismatches(conn, limit: int) -> Tuple[int, List[dict]]:
    """Torrents whose ``VideoCode`` disagrees with their parent movie's."""
    total = _count(conn, "SELECT COUNT(*) AS n" + _MISMATCH_WHERE)
    rows = _dicts(conn.execute(
        """SELECT rt.Id AS TorrentId, rt.ReportMovieId AS AttachedMovieId,
                  rt.VideoCode AS TorrentVideoCode, rt.MagnetUri AS MagnetUri,
                  rt.SubtitleIndicator AS SubtitleIndicator,
                  rt.CensorIndicator AS CensorIndicator,
                  rm.VideoCode AS AttachedMovieVideoCode,
                  rm.SessionId AS AttachedSessionId"""
        + _MISMATCH_WHERE
        + " ORDER BY rt.Id LIMIT ?",
        (limit,),
    ).fetchall())
    return total, rows


def _movies_by_code(conn, codes: Set[str]) -> Dict[str, List[dict]]:
    """Candidate intended parents, grouped by ``VideoCode``."""
    out: Dict[str, List[dict]] = {}
    ordered = sorted(c for c in codes if c)
    for chunk in _chunks(ordered):
        placeholders = ",".join(["?"] * len(chunk))
        rows = _dicts(conn.execute(
            "SELECT Id, SessionId, VideoCode FROM ReportMovies "
            f"WHERE VideoCode IN ({placeholders})",
            tuple(chunk),
        ).fetchall())
        for row in rows:
            out.setdefault(row["VideoCode"], []).append(row)
    return out


def _children_by_movie(conn, movie_ids: Iterable[Any]) -> Dict[Any, List[dict]]:
    """Existing torrents of each movie id, so occupied slots are visible."""
    out: Dict[Any, List[dict]] = {}
    ordered = sorted({m for m in movie_ids if m is not None})
    for chunk in _chunks(ordered):
        placeholders = ",".join(["?"] * len(chunk))
        rows = _dicts(conn.execute(
            "SELECT Id, ReportMovieId, SubtitleIndicator, CensorIndicator, "
            f"MagnetUri FROM ReportTorrents WHERE ReportMovieId IN ({placeholders})",
            tuple(chunk),
        ).fetchall())
        for row in rows:
            out.setdefault(row["ReportMovieId"], []).append(row)
    return out


def _movies_by_id(conn, movie_ids: Iterable[Any]) -> Dict[Any, dict]:
    out: Dict[Any, dict] = {}
    ordered = sorted({m for m in movie_ids if m is not None})
    for chunk in _chunks(ordered):
        placeholders = ",".join(["?"] * len(chunk))
        rows = _dicts(conn.execute(
            "SELECT Id, SessionId, VideoCode FROM ReportMovies "
            f"WHERE Id IN ({placeholders})",
            tuple(chunk),
        ).fetchall())
        for row in rows:
            out[row["Id"]] = row
    return out


def _suggested_update_sql(torrent_id: Any, movie_id: Any) -> str:
    """The exact statement an operator runs *after* verifying provenance.

    Both columns are ``INTEGER PRIMARY KEY`` values read straight back
    from this database, so they are interpolated as-is; this string is
    printed for a human, never executed by this tool.
    """
    return (
        f"UPDATE ReportTorrents SET ReportMovieId = {movie_id} "
        f"WHERE Id = {torrent_id};"
    )


def classify_mismatches(
    conn, mismatches: List[dict],
) -> Tuple[List[dict], List[dict]]:
    """Give every mismatch a reason, and a suggested parent where one exists.

    Returns ``(suggestions, unrepairable)``. **Neither list is
    auto-repairable** — ``unrepairable`` holds every mismatch row tagged
    with the reason it is report-only, and ``suggestions`` is the printable
    subset that additionally carries a candidate parent and the ``UPDATE``
    an operator could run by hand.

    A candidate is suggested only when exactly one ``ReportMovies`` row
    carries the torrent's ``VideoCode`` *and* that candidate has neither
    the same ``MagnetUri`` nor a torrent already occupying the torrent's
    ``(SubtitleIndicator, CensorIndicator)`` slot. The bug leaves the
    correct parent childless on the damaged backend, so a free slot is the
    expected shape — but "expected shape" is not provenance, and
    ``ReportTorrents`` carries no ``SessionId``/timestamp that could supply
    it, so the suggestion stays a suggestion
    (:data:`REASON_SINGLE_CANDIDATE`; see the module docstring for why the
    wrong-answer case is common rather than exotic).
    """
    if not mismatches:
        return [], []

    codes = {m["TorrentVideoCode"] for m in mismatches}
    candidates = _movies_by_code(conn, codes)
    candidate_ids = {c["Id"] for group in candidates.values() for c in group}
    children = _children_by_movie(conn, candidate_ids)

    suggestions: List[dict] = []
    unrepairable: List[dict] = []
    for m in mismatches:
        code = m["TorrentVideoCode"]
        # The attached parent has a different VideoCode by definition, so
        # it can never be its own candidate; the filter is belt-and-braces.
        pool = [
            c for c in candidates.get(code, [])
            if c["Id"] != m["AttachedMovieId"]
        ]
        reason: Optional[str] = None
        extra: Dict[str, Any] = {}
        if not pool:
            reason = REASON_NO_CANDIDATE
        elif len(pool) > 1:
            reason = REASON_MULTIPLE_CANDIDATES
        else:
            target = pool[0]
            siblings = children.get(target["Id"], [])
            magnet = m.get("MagnetUri") or ""
            if magnet and any((s.get("MagnetUri") or "") == magnet for s in siblings):
                reason = REASON_MAGNET_PRESENT
            elif any(_slot(s) == _slot(m) for s in siblings):
                reason = REASON_SLOT_OCCUPIED
            else:
                reason = REASON_SINGLE_CANDIDATE
                suggestions.append({
                    "torrent_id": m["TorrentId"],
                    "video_code": code,
                    "from_movie_id": m["AttachedMovieId"],
                    "from_session_id": m.get("AttachedSessionId"),
                    "to_movie_id": target["Id"],
                    "to_session_id": target.get("SessionId"),
                    "reason": reason,
                    "caveat": MANUAL_CORROBORATION_NOTE,
                    "sql": _suggested_update_sql(m["TorrentId"], target["Id"]),
                })
                extra = {
                    "SuggestedMovieId": target["Id"],
                    "SuggestedSessionId": target.get("SessionId"),
                }
                # Book the slot so a second mismatch is not suggested into
                # the same (movie, slot) within one run.
                children.setdefault(target["Id"], []).append(m)
        unrepairable.append({**m, "reason": reason, **extra})
    return suggestions, unrepairable


def find_duplicate_slots(
    conn, limit: int, explained_torrent_ids: Set[Any],
) -> Tuple[int, int, List[dict]]:
    """Movies holding two+ torrents in one category slot.

    Returns ``(groups_total, unexplained_count, samples)``. Groups whose
    surplus is fully accounted for by :data:`SIGNAL_CODE_MISMATCH` rows
    are dropped — those are the same damage seen from the parent side.
    """
    group_from = """
        FROM ReportTorrents rt
        WHERE EXISTS (
            SELECT 1 FROM ReportMovies rm WHERE rm.Id = rt.ReportMovieId
        )
        GROUP BY rt.ReportMovieId, rt.SubtitleIndicator, rt.CensorIndicator
        HAVING COUNT(*) > 1
    """
    groups_total = _count(
        conn,
        "SELECT COUNT(*) AS n FROM (SELECT 1" + group_from + ")",
    )
    groups = _dicts(conn.execute(
        """SELECT rt.ReportMovieId AS AttachedMovieId,
                  rt.SubtitleIndicator AS SubtitleIndicator,
                  rt.CensorIndicator AS CensorIndicator,
                  COUNT(*) AS TorrentCount"""
        + group_from
        + " ORDER BY COUNT(*) DESC, rt.ReportMovieId LIMIT ?",
        (limit,),
    ).fetchall())
    if not groups:
        return groups_total, 0, []

    children = _children_by_movie(conn, {g["AttachedMovieId"] for g in groups})
    movies = _movies_by_id(conn, {g["AttachedMovieId"] for g in groups})

    findings: List[dict] = []
    for g in groups:
        members = [
            c for c in children.get(g["AttachedMovieId"], [])
            if _slot(c) == _slot(g)
        ]
        residual = [c for c in members if c["Id"] not in explained_torrent_ids]
        if len(residual) < 2:
            continue
        movie = movies.get(g["AttachedMovieId"], {})
        findings.append({
            "AttachedMovieId": g["AttachedMovieId"],
            "AttachedSessionId": movie.get("SessionId"),
            "AttachedMovieVideoCode": movie.get("VideoCode"),
            "SubtitleIndicator": g["SubtitleIndicator"],
            "CensorIndicator": g["CensorIndicator"],
            "TorrentCount": g["TorrentCount"],
            "UnexplainedTorrentIds": [c["Id"] for c in residual],
        })
    return groups_total, len(findings), findings


# ── audit ───────────────────────────────────────────────────────────────


def audit(conn, *, max_findings: int = 5000, samples: int = 10) -> dict:
    """Read-only assessment. Returns a JSON-able result dict."""
    orphan_total, orphan_rows = find_orphans(conn, max_findings)
    mismatch_total, mismatch_rows = find_code_mismatches(conn, max_findings)
    suggestions, ambiguous = classify_mismatches(conn, mismatch_rows)
    explained = {m["TorrentId"] for m in mismatch_rows}
    dup_total, dup_unexplained, dup_rows = find_duplicate_slots(
        conn, max_findings, explained,
    )

    ambiguous_by_reason: Dict[str, int] = {}
    for row in ambiguous:
        reason = row["reason"]
        ambiguous_by_reason[reason] = ambiguous_by_reason.get(reason, 0) + 1

    affected_sessions: Set[str] = set()
    for row in mismatch_rows:
        if row.get("AttachedSessionId") is not None:
            affected_sessions.add(str(row["AttachedSessionId"]))
    for row in suggestions:
        if row.get("to_session_id") is not None:
            affected_sessions.add(str(row["to_session_id"]))
    for row in dup_rows:
        if row.get("AttachedSessionId") is not None:
            affected_sessions.add(str(row["AttachedSessionId"]))

    signals = {
        SIGNAL_ORPHAN: {
            "confidence": SIGNAL_CONFIDENCE[SIGNAL_ORPHAN],
            "count": orphan_total,
            "truncated": orphan_total > len(orphan_rows),
            "samples": orphan_rows[:samples],
        },
        SIGNAL_CODE_MISMATCH: {
            "confidence": SIGNAL_CONFIDENCE[SIGNAL_CODE_MISMATCH],
            "count": mismatch_total,
            "truncated": mismatch_total > len(mismatch_rows),
            # Report-only: a count of printed suggestions, not of repairs
            # --apply will make. There is no auto-repairable subset.
            "suggested_manual_repairs": len(suggestions),
            "ambiguous": len(ambiguous),
            "ambiguous_by_reason": ambiguous_by_reason,
            "samples": mismatch_rows[:samples],
            "ambiguous_samples": ambiguous[:samples],
        },
        SIGNAL_DUPLICATE_SLOT: {
            "confidence": SIGNAL_CONFIDENCE[SIGNAL_DUPLICATE_SLOT],
            "count": dup_unexplained,
            "duplicate_groups_total": dup_total,
            # Compared against the fetch bound, not against the filtered
            # findings — dropping groups already explained by a mismatch is
            # not truncation.
            "truncated": dup_total > max_findings,
            "samples": dup_rows[:samples],
        },
    }

    findings_total = orphan_total + mismatch_total + dup_unexplained
    # Every finding needs a human now: VIDEO_CODE_MISMATCH re-attachment is
    # report-only (provenance is unprovable from this schema), the
    # duplicate-slot signal is heuristic, and the orphan DELETE is an
    # explicit, irreversible opt-in. Nothing is left that --apply on its own
    # promises to fix, so there is no third verdict — see VERDICT_EXIT_CODE
    # for why exit 1 stays unassigned.
    verdict = VERDICT_CLEAN if findings_total == 0 else VERDICT_ESCALATE

    return {
        "kind": "report_torrent_fk_audit",
        "signals": signals,
        "findings_total": findings_total,
        # Only orphan deletion is executable, and only with
        # --apply --force --delete-orphans. Re-attachment lives in
        # "manual_repair_suggestions" and is never executed by this tool.
        "repair_plan": {
            "delete_orphans": [r["TorrentId"] for r in orphan_rows],
        },
        "manual_repair_suggestions": suggestions,
        "unverifiable": {
            "torrents_without_comparable_video_code": _count(
                conn, "SELECT COUNT(*) AS n" + _UNVERIFIABLE_WHERE,
            ),
        },
        "affected_session_ids": sorted(affected_sessions),
        "verdict": verdict,
    }


def apply_repairs(conn, plan: dict, *, delete_orphans: bool) -> dict:
    """Execute *plan*. Mutates the DB. Orphan deletion is all there is.

    This function deliberately knows nothing about re-attachment: no
    ``UPDATE ReportTorrents SET ReportMovieId`` is issued anywhere in this
    tool, because no available evidence identifies the intended parent
    (module docstring, "Repair"). Suggestions are printed for a human.

    Counts come from the plan, not from ``cursor.rowcount``: under
    ``STORAGE_BACKEND=d1`` writes are queued until ``commit()`` /
    ``flush()``, so a fresh cursor's rowcount is not a reliable tally.
    """
    deleted = 0
    orphan_ids = list(plan.get("delete_orphans") or [])
    if delete_orphans and orphan_ids:
        for chunk in _chunks(orphan_ids):
            placeholders = ",".join(["?"] * len(chunk))
            conn.execute(
                f"DELETE FROM ReportTorrents WHERE Id IN ({placeholders})",
                tuple(chunk),
            )
        deleted = len(orphan_ids)

    return {
        "orphans_deleted": deleted,
        "orphans_left": 0 if delete_orphans else len(orphan_ids),
    }


# ── CLI ─────────────────────────────────────────────────────────────────


def _short(value: Any, width: int = 40) -> Any:
    """Truncate long strings (magnets) for log lines; leave scalars alone."""
    if not isinstance(value, str) or len(value) <= width:
        return value
    return value[:width - 1] + "…"


def _log_manual_repair_suggestions(result: dict, *, samples: int) -> None:
    """Print the re-attachments an operator may make *after* verifying them.

    Emitted in every mode, including under ``--apply``: this tool never
    executes them, so they remain outstanding work either way.
    """
    suggestions = result.get("manual_repair_suggestions") or []
    if not suggestions:
        return
    logger.warning(
        "%d %s row(s) have exactly one candidate parent (reason=%s). NOT "
        "auto-repaired: ReportTorrents carries no SessionId and no "
        "timestamp, so 'currently the only movie with this code' is not "
        "provenance — and because the mis-pointed row escapes session "
        "rollback while its true parent does not, the surviving candidate "
        "is often a LATER re-scrape of the same VideoCode. Corroborate each "
        "one first (the committed dated DailyReport CSVs in git history "
        "record which videos/magnets belonged to which day), then apply by "
        "hand:",
        len(suggestions), SIGNAL_CODE_MISMATCH, REASON_SINGLE_CANDIDATE,
    )
    for row in suggestions[:samples]:
        logger.warning(
            "  [manual-repair] torrent=%s (%s) movie %s (session=%s) -> %s "
            "(session=%s): %s",
            row["torrent_id"], row["video_code"], row["from_movie_id"],
            row["from_session_id"], row["to_movie_id"], row["to_session_id"],
            row["sql"],
        )
    if len(suggestions) > samples:
        logger.warning(
            "  [manual-repair] +%d more — use --json for the full list.",
            len(suggestions) - samples,
        )


def _log_report(
    result: dict,
    *,
    samples: int,
    dry_run: bool,
    title: str = "ReportTorrents FK Audit",
) -> None:
    signals = result["signals"]
    mismatch = signals[SIGNAL_CODE_MISMATCH]
    log_summary_block(logger, title, {
        "Backend": current_backend(),
        f"{SIGNAL_ORPHAN} (deterministic)": signals[SIGNAL_ORPHAN]["count"],
        f"{SIGNAL_CODE_MISMATCH} (deterministic)": mismatch["count"],
        "  needs manual review (never auto-repaired)": mismatch["ambiguous"],
        "  of which a parent is suggested": (
            mismatch["suggested_manual_repairs"]
        ),
        f"{SIGNAL_DUPLICATE_SLOT} (heuristic)": (
            signals[SIGNAL_DUPLICATE_SLOT]["count"]
        ),
        "Unverifiable (no VideoCode)": (
            result["unverifiable"]["torrents_without_comparable_video_code"]
        ),
        "Verdict": result["verdict"],
    })

    for name in (SIGNAL_ORPHAN, SIGNAL_CODE_MISMATCH, SIGNAL_DUPLICATE_SLOT):
        signal = signals[name]
        if not signal["count"]:
            continue
        if signal["truncated"]:
            logger.warning(
                "%s: %d row(s) found, detail fetch truncated — raise "
                "--max-findings for a complete plan.", name, signal["count"],
            )
        for row in signal["samples"]:
            logger.info("  [%s] %s", name, json.dumps(
                {k: _short(v) for k, v in row.items()}, ensure_ascii=False,
            ))

    for row in mismatch["ambiguous_samples"]:
        logger.warning(
            "  [%s] torrent=%s left alone: %s (code=%s attached to movie=%s "
            "of session=%s carrying code=%s)",
            SIGNAL_CODE_MISMATCH, row["TorrentId"], row["reason"],
            row["TorrentVideoCode"], row["AttachedMovieId"],
            row.get("AttachedSessionId"), row.get("AttachedMovieVideoCode"),
        )

    _log_manual_repair_suggestions(result, samples=samples)

    sessions = result["affected_session_ids"]
    if sessions:
        shown = sessions[:samples]
        suffix = (
            f" (+{len(sessions) - len(shown)} more)"
            if len(sessions) > len(shown) else ""
        )
        logger.info(
            "Affected SessionIds (%d): %s%s",
            len(sessions), ", ".join(shown), suffix,
        )

    orphans = result["repair_plan"]["delete_orphans"]
    if dry_run and orphans:
        logger.info(
            "[dry-run] %d orphaned torrent(s) would be DELETED with "
            "--apply --force --delete-orphans (no correct parent survives, "
            "so deletion is the only safe repair).", len(orphans),
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.audit_report_torrent_fks",
        description=(
            "Assess historical BFR-034 damage: ReportTorrents rows attached "
            "to another session's ReportMovies row. Read-only by default; "
            "the only executable repair is deleting orphaned torrents. "
            "Re-attachment is suggested as SQL for manual review, never "
            "applied, because no column in ReportTorrents proves which "
            "session a torrent belonged to."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the full result as JSON for programmatic consumption.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        metavar="N",
        help="Sample rows logged per signal (default: 10).",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=5000,
        metavar="N",
        help=(
            "Cap on detail rows fetched per signal (default: 5000). Counts "
            "are always exact; the plan is truncated past this bound."
        ),
    )

    repair = parser.add_argument_group("repair options")
    repair.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Execute the repairs. Only orphan deletion is executable (and "
            "only with --delete-orphans); VideoCode mismatches are always "
            "report-only, since nothing proves their intended parent. "
            "Requires --force."
        ),
    )
    repair.add_argument(
        "--force",
        action="store_true",
        help="Confirm that --apply may write to the resolved backend.",
    )
    repair.add_argument(
        "--delete-orphans",
        action="store_true",
        help=(
            "Also DELETE orphaned torrents (irreversible; their correct "
            "parent no longer exists). Requires --apply --force."
        ),
    )
    repair.add_argument(
        "--max-repairs",
        type=int,
        default=500,
        metavar="N",
        help=(
            "Refuse --apply when the executable plan (orphan DELETEs) "
            "exceeds N rows (default: 500). Guards against an unexpectedly "
            "large blast radius."
        ),
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.apply and not args.force:
        parser.error(
            "--apply writes to the resolved STORAGE_BACKEND; pass --force to "
            "confirm (run without --apply first and read the plan)."
        )
    if args.force and not args.apply:
        parser.error("--force is only meaningful together with --apply.")
    if args.delete_orphans and not args.apply:
        parser.error("--delete-orphans requires --apply --force.")
    setup_logging(log_level=args.log_level)

    try:
        with get_db(_db.REPORTS_DB_PATH) as conn:
            result = audit(
                conn,
                max_findings=args.max_findings,
                samples=args.samples,
            )
            plan = result["repair_plan"]
            # Orphan DELETEs are the only executable repair, and only when
            # --delete-orphans is present; without it --apply writes nothing.
            planned = (
                len(plan["delete_orphans"]) if args.delete_orphans else 0
            )
            if args.apply and planned > args.max_repairs:
                # Programmatic consumers must be able to tell a safety-rail
                # refusal from an ordinary ESCALATE verdict, and --json must
                # emit a payload on this path too.
                result["refused"] = {
                    "reason": "max_repairs_exceeded",
                    "planned": planned,
                    "max_repairs": args.max_repairs,
                }
                if args.json_output:
                    print(json.dumps(
                        result, ensure_ascii=False, indent=2, default=str,
                    ))
                else:
                    _log_report(result, samples=args.samples, dry_run=True)
                logger.error(
                    "REFUSED: plan touches %d row(s), above --max-repairs=%d. "
                    "Review the findings, then raise the rail deliberately.",
                    planned, args.max_repairs,
                )
                return VERDICT_EXIT_CODE[VERDICT_ESCALATE]

            if args.apply:
                result["applied"] = apply_repairs(
                    conn, plan, delete_orphans=args.delete_orphans,
                )
                # Flush before re-auditing: under STORAGE_BACKEND=d1 the
                # writes above are queued until commit(), so a re-audit on
                # the same connection would otherwise read pre-repair state.
                conn.commit()
                result["post_apply"] = audit(
                    conn,
                    max_findings=args.max_findings,
                    samples=args.samples,
                )
    except Exception as exc:  # noqa: BLE001 — any backend error is exit 3
        logger.error(
            "Could not audit %s: %s", _db.REPORTS_DB_PATH, exc, exc_info=True,
        )
        return EXIT_DB_ERROR

    final = result.get("post_apply", result)
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _log_report(
            result,
            samples=args.samples,
            dry_run=not args.apply,
            title=(
                "ReportTorrents FK Audit (before repair)" if args.apply
                else "ReportTorrents FK Audit"
            ),
        )
        if args.apply:
            applied = result["applied"]
            logger.info(
                "Applied: %d orphan deletion(s) (%d orphan(s) left in "
                "place). Re-attachment is report-only: 0 FK(s) rewritten.",
                applied["orphans_deleted"], applied["orphans_left"],
            )
            _log_report(
                final,
                samples=args.samples,
                dry_run=False,
                title="ReportTorrents FK Audit (after repair)",
            )

    return VERDICT_EXIT_CODE[final["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
