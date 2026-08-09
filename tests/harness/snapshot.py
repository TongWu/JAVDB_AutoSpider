"""Normalized golden-run snapshot capture + diff (ADR-037 D7, Phase 3).

``capture_snapshot`` projects a harness run into a deterministic dict of the
pipeline's authoritative outputs, EXCLUDING every nondeterministic field
(session id, timestamps, autoincrement ids, event seq). ``diff_snapshots``
returns a human-readable list of the keys that differ — empty means identical.
"""

from __future__ import annotations

from typing import Any


def capture_snapshot(pipeline_harness, result) -> dict:
    """Project a run into a normalized, diff-stable snapshot."""
    from javdb.storage import db as _db
    from javdb.storage.db import get_db

    # MovieHistory / TorrentHistory live in the history DB. Pass HISTORY_DB_PATH
    # explicitly (get_db's contract is a filesystem PATH, not a logical name) and
    # resolve it at call time via _db so the read honours the autouse
    # _isolate_sqlite repath — consistent with events()/acquisition_outcomes().
    with get_db(_db.HISTORY_DB_PATH) as conn:
        movies = [
            {"video_code": row[0], "href": row[1]}
            for row in conn.execute(
                "SELECT VideoCode, Href FROM MovieHistory ORDER BY Href"
            ).fetchall()
        ]
        torrents = sorted(
            row[0]
            for row in conn.execute(
                "SELECT MagnetUri FROM TorrentHistory"
            ).fetchall()
            if row[0]
        )

    acquisition = sorted(
        ({"qb_hash": o["qb_hash"], "state": o["state"]}
         for o in pipeline_harness.acquisition_outcomes()),
        key=lambda d: d["qb_hash"],
    )

    return {
        "movies": movies,
        "torrents": torrents,
        "qb_hashes": sorted(result.qb.all_hashes()),
        "acquisition": acquisition,
        "events": list(pipeline_harness.events()),  # seq order; semantically a sequence
    }


def diff_snapshots(expected: dict, actual: dict) -> list[str]:
    """Return one readable line per differing top-level key (empty == equal)."""
    diffs: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        exp: Any = expected.get(key)
        act: Any = actual.get(key)
        if exp != act:
            diffs.append(f"{key}: expected {exp!r}, got {act!r}")
    return diffs
