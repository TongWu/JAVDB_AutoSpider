#!/usr/bin/env python3
"""One-off cleanup: delete orphaned session-tagged rows from D1.

A row is *orphaned* when its ``SessionId`` / ``session_id`` value has no
matching ``ReportSessions.Id`` parent — the residue left behind when a
session was rolled back or its ReportSessions row was deleted/cleaned up.
These surface as ``PRAGMA foreign_key_check`` violations in the reports DB
(the four FK children) plus dangling rows in the history / operations DBs.

Scope mirrors ``javdb.storage.db._db_rollback`` exactly: only the
session-OWNED tables a rollback deletes are cleaned here.

    history     MovieHistory (+ its TorrentHistory children),
                TorrentHistory, PendingMovieHistoryWrites,
                PendingTorrentHistoryWrites
    reports     ReportMovies (+ ReportTorrents children), SpiderStats,
                UploaderStats, PikpakStats
    operations  DedupRecords, PikpakHistory, InventoryAlignNoExactMatch

History uses a movie-aggregate cascade: ``TorrentHistory`` has an enforced
``REFERENCES MovieHistory(Id)`` (no ON DELETE), so an orphan movie's torrent
children — INCLUDING any with a NULL ``SessionId`` — are deleted first, then
the movie. ``TorrentHistory`` rows orphaned by their own ``SessionId`` (whose
movie may survive) are deleted too.

Deliberately EXCLUDED — these mirror ``ROLLBACK_PRESERVED_TABLES``. Their
``session_id`` is PROVENANCE, not an ownership FK; they are enrichment /
audit / projection records that legitimately outlive a rolled-back session,
carry no FK to ReportSessions, and so never violate foreign_key_check.
Deleting them would drop the failed run's own record or orphan a live
external resource from its tracker:
    reports     PipelineEvent     (ADR-036 append-only event spine; SessionFailed)
                RunEventSummary   (ADR-036 projection of that spine)
                ParseRunFieldFill (ADR-035 enrichment, off the commit path)
                OpsIncidents      (ADR-035/026 the run's own failure diagnosis)
    operations  AcquisitionOutcome      (ADR-033 D10 bypasses session/rollback;
                                         keyed by qb_hash — the reconcile loop
                                         tracks the real qB torrent's fate)
                EmailNotificationHistory (records emails really sent)

Usage::

    # dry-run (default): enumerate orphans per table, no writes
    python3 -m javdb.migrations.tools.cleanup_orphaned_session_rows

    # apply the deletions, then re-check foreign_key_check
    python3 -m javdb.migrations.tools.cleanup_orphaned_session_rows --apply

Credentials are read from ``config.py`` (CLOUDFLARE_ACCOUNT_ID /
CLOUDFLARE_API_TOKEN / D1_*_DB_ID), same as the rest of the D1 tooling.
"""

from __future__ import annotations

import argparse
import sys

import requests

import config

_ACCOUNT = config.CLOUDFLARE_ACCOUNT_ID
_TOKEN = config.CLOUDFLARE_API_TOKEN
_HEADERS = {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}

HISTORY = config.D1_HISTORY_DB_ID
REPORTS = config.D1_REPORTS_DB_ID
OPERATIONS = config.D1_OPERATIONS_DB_ID

# Flat (db, table, column) orphan deletes — no FK ordering concerns. The reports
# FK children reference an already-missing ReportSessions parent, so deleting
# them only REMOVES violations; operations tables carry no FK.
REPORTS_FLAT = [
    ("ReportMovies", "SessionId"),      # ReportTorrents children pruned first
    ("SpiderStats", "SessionId"),
    ("UploaderStats", "SessionId"),
    ("PikpakStats", "SessionId"),
]
OPERATIONS_FLAT = [
    ("DedupRecords", "SessionId"),
    ("PikpakHistory", "SessionId"),
    ("InventoryAlignNoExactMatch", "SessionId"),
]
# Pending history tables: no FK, flat delete by SessionId.
HISTORY_FLAT = [
    ("PendingMovieHistoryWrites", "SessionId"),
    ("PendingTorrentHistoryWrites", "SessionId"),
]

# Provenance/enrichment logs intentionally left untouched (see module docstring;
# mirrors javdb.storage.db._db_rollback.ROLLBACK_PRESERVED_TABLES).
EXCLUDED_APPEND_ONLY = [
    (REPORTS, "PipelineEvent", "session_id"),
    (REPORTS, "RunEventSummary", "session_id"),
    (REPORTS, "ParseRunFieldFill", "session_id"),
    (REPORTS, "OpsIncidents", "session_id"),
    (OPERATIONS, "AcquisitionOutcome", "session_id"),
    (OPERATIONS, "EmailNotificationHistory", "SessionId"),
]

# D1 caps bound SQL variables per statement; chunk IN-lists.
_CHUNK = 90


def _q(db_id: str, sql: str, params=None):
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}"
        f"/d1/database/{db_id}/query"
    )
    resp = requests.post(
        url, headers=_HEADERS, json={"sql": sql, "params": params or []}, timeout=60,
    )
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"D1 query failed: {body.get('errors')}\nSQL: {sql}")
    return body["result"][0]["results"]


def _chunks(seq, n=_CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _parent_ids() -> set[str]:
    return {str(r["Id"]) for r in _q(REPORTS, "SELECT Id FROM ReportSessions")}


def _orphan_sids(db_id: str, table: str, col: str, parents: set[str]) -> dict[str, int]:
    """Distinct non-NULL session ids in (table, col) with no parent → {sid: rows}."""
    rows = _q(db_id, f"SELECT {col} AS v, COUNT(*) AS n FROM {table} GROUP BY {col}")
    return {
        str(r["v"]): r["n"]
        for r in rows
        if r["v"] is not None and str(r["v"]) not in parents
    }


def _delete_in(db_id: str, table: str, col: str, values: list) -> None:
    for chunk in _chunks([str(v) for v in values]):
        ph = ",".join(["?"] * len(chunk))
        _q(db_id, f"DELETE FROM {table} WHERE {col} IN ({ph})", chunk)


# ── History (movie-aggregate cascade) ───────────────────────────────────


def _plan_history(parents: set[str]) -> dict:
    """Resolve the exact MovieHistory / TorrentHistory ids to delete."""
    mh = _q(HISTORY, "SELECT Id, SessionId FROM MovieHistory WHERE SessionId IS NOT NULL")
    orphan_mh_ids = [r["Id"] for r in mh if str(r["SessionId"]) not in parents]

    # Every torrent child of an orphan movie (any SessionId, incl. NULL).
    th_child_ids: list = []
    for chunk in _chunks(orphan_mh_ids):
        ph = ",".join(["?"] * len(chunk))
        th_child_ids += [
            r["Id"] for r in _q(
                HISTORY,
                f"SELECT Id FROM TorrentHistory WHERE MovieHistoryId IN ({ph})",
                [str(x) for x in chunk],
            )
        ]
    # Torrents orphaned by their own SessionId (their movie may survive).
    th = _q(HISTORY, "SELECT Id, SessionId FROM TorrentHistory WHERE SessionId IS NOT NULL")
    th_by_session_ids = [r["Id"] for r in th if str(r["SessionId"]) not in parents]

    th_ids = sorted(set(th_child_ids) | set(th_by_session_ids))
    return {
        "movie_ids": sorted(orphan_mh_ids),
        "torrent_ids": th_ids,
        "torrent_children": sorted(set(th_child_ids)),
        "torrent_by_session": sorted(set(th_by_session_ids)),
        "pending": {
            t: _orphan_sids(HISTORY, t, c, parents) for t, c in HISTORY_FLAT
        },
    }


def _apply_history(plan: dict) -> None:
    # Torrents before movies (enforced TorrentHistory -> MovieHistory FK).
    if plan["torrent_ids"]:
        _delete_in(HISTORY, "TorrentHistory", "Id", plan["torrent_ids"])
        print(f"  TorrentHistory                 deleted {len(plan['torrent_ids'])} row(s)")
    if plan["movie_ids"]:
        _delete_in(HISTORY, "MovieHistory", "Id", plan["movie_ids"])
        print(f"  MovieHistory                   deleted {len(plan['movie_ids'])} row(s)")
    for table, col in HISTORY_FLAT:
        sids = sorted(plan["pending"][table])
        if sids:
            _delete_in(HISTORY, table, col, sids)
            n = sum(plan["pending"][table].values())
            print(f"  {table:30} deleted {n} row(s)")


# ── Reports / operations (flat orphan deletes) ──────────────────────────


def _count_report_torrents(orphan_movie_sids: list[str]) -> int:
    total = 0
    for chunk in _chunks(orphan_movie_sids):
        ph = ",".join(["?"] * len(chunk))
        total += _q(
            REPORTS,
            f"SELECT COUNT(*) AS n FROM ReportTorrents WHERE ReportMovieId IN "
            f"(SELECT Id FROM ReportMovies WHERE SessionId IN ({ph}))",
            chunk,
        )[0]["n"]
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--apply", action="store_true", help="Execute the DELETEs (default: dry-run).",
    )
    args = ap.parse_args(argv)

    parents = _parent_ids()
    print(f"ReportSessions parent ids: {len(parents)}\n")

    total = 0
    print(f"{'db':10} {'table':30} {'rows':>8}")
    print("-" * 52)

    # History (movie-aggregate).
    hist = _plan_history(parents)
    if hist["torrent_ids"]:
        total += len(hist["torrent_ids"])
        print(f"{'history':10} {'TorrentHistory':30} {len(hist['torrent_ids']):>8}"
              f"   (by-session {len(hist['torrent_by_session'])}, "
              f"movie-children {len(hist['torrent_children'])})")
    if hist["movie_ids"]:
        total += len(hist["movie_ids"])
        print(f"{'history':10} {'MovieHistory':30} {len(hist['movie_ids']):>8}")
    for table, _c in HISTORY_FLAT:
        n = sum(hist["pending"][table].values())
        if n:
            total += n
            print(f"{'history':10} {table:30} {n:>8}")

    # Reports flat.
    reports_plan = {t: _orphan_sids(REPORTS, t, c, parents) for t, c in REPORTS_FLAT}
    orphan_movie_sids = sorted(reports_plan["ReportMovies"])
    rt_children = _count_report_torrents(orphan_movie_sids) if orphan_movie_sids else 0
    if rt_children:
        total += rt_children
        print(f"{'reports':10} {'ReportTorrents (via movie)':30} {rt_children:>8}")
    for table, _c in REPORTS_FLAT:
        n = sum(reports_plan[table].values())
        if n:
            total += n
            print(f"{'reports':10} {table:30} {n:>8}")

    # Operations flat.
    ops_plan = {t: _orphan_sids(OPERATIONS, t, c, parents) for t, c in OPERATIONS_FLAT}
    for table, _c in OPERATIONS_FLAT:
        n = sum(ops_plan[table].values())
        if n:
            total += n
            print(f"{'operations':10} {table:30} {n:>8}")

    print("-" * 52)
    print(f"TOTAL orphan rows to delete: {total}\n")

    print("Append-only logs left untouched (provenance, not deleted):")
    for db_id, table, col in EXCLUDED_APPEND_ONLY:
        skipped = _orphan_sids(db_id, table, col, parents)
        print(f"  {table:25} provenance session_ids without parent: "
              f"{len(skipped)} ({sum(skipped.values())} rows)")
    print()

    if not args.apply:
        print("(dry-run only — re-run with --apply to execute)")
        return 0

    print("Applying deletions...")
    _apply_history(hist)
    # ReportTorrents children of orphan movies first, then the flat reports tables.
    if orphan_movie_sids and rt_children:
        for chunk in _chunks(orphan_movie_sids):
            ph = ",".join(["?"] * len(chunk))
            _q(REPORTS,
               f"DELETE FROM ReportTorrents WHERE ReportMovieId IN "
               f"(SELECT Id FROM ReportMovies WHERE SessionId IN ({ph}))", chunk)
        print(f"  ReportTorrents                 deleted {rt_children} row(s)")
    for table, col in REPORTS_FLAT:
        sids = sorted(reports_plan[table])
        if sids:
            _delete_in(REPORTS, table, col, sids)
            print(f"  {table:30} deleted {sum(reports_plan[table].values())} row(s)")
    for table, col in OPERATIONS_FLAT:
        sids = sorted(ops_plan[table])
        if sids:
            _delete_in(OPERATIONS, table, col, sids)
            print(f"  {table:30} deleted {sum(ops_plan[table].values())} row(s)")

    fkc = _q(REPORTS, "PRAGMA foreign_key_check")
    print(f"\nPRAGMA foreign_key_check (reports) after cleanup: {len(fkc)} violation(s)")
    for r in fkc:
        print("   ", r)
    if fkc:
        print("FAIL — foreign_key_check still reports violations.")
        return 1
    print("OK — reports foreign_key_check is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
