#!/usr/bin/env python3
"""One-time migration: normalize JavDB URL columns to absolute BASE_URL form.

Backend-aware: routes through :func:`get_db`, so it targets whatever
``STORAGE_BACKEND`` is active. D1 is the canonical source of truth, so run it
with ``STORAGE_BACKEND=d1`` to correct production data.

Targets:
  - history ``MovieHistory``: ``Href``, ``ActorLink``, ``SupportingActors``
    (JSON ``link`` / ``href`` payloads)
  - optional reports ``ReportMovies``: ``Href`` (``--also-reports-db``)

Only rows that still carry a *site-relative* value are fetched and rewritten;
already-absolute rows are skipped, so a second run is a cheap no-op. See
``docs/design/BFR-010-Relative-Href-Inconsistency/``.

Usage::

    STORAGE_BACKEND=d1 python3 -m javdb.migrations.tools.absolutize_javdb_urls_in_history --dry-run
    STORAGE_BACKEND=d1 python3 -m javdb.migrations.tools.absolutize_javdb_urls_in_history --apply
    STORAGE_BACKEND=d1 python3 -m javdb.migrations.tools.absolutize_javdb_urls_in_history --apply --also-reports-db
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Tuple

from javdb.parsing.common import (
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from javdb.infra.config import cfg
from javdb.infra.logging import get_logger, setup_logging
from javdb.storage.db import get_db, HISTORY_DB_PATH, REPORTS_DB_PATH

setup_logging()
logger = get_logger(__name__)


@dataclass
class RunStats:
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0


def _chunked(seq: List, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# A row needs work only if some URL column still holds a site-relative value.
# Absolute values begin with ``https://`` so they never contain a quote
# immediately followed by a slash (``"/``); a relative ActorLink/Href begins
# with ``/`` and a relative SupportingActors inner link reads ``"link": "/..``.
_HISTORY_CANDIDATE_SQL = """
    SELECT Id AS Id,
           Href AS Href,
           ActorLink AS ActorLink,
           SupportingActors AS SupportingActors
    FROM   MovieHistory
    WHERE  Href LIKE '/%'
       OR  ActorLink LIKE '/%'
       OR  SupportingActors LIKE '%"/%'
"""


def _process_history(base_url: str, dry_run: bool) -> RunStats:
    stats = RunStats()
    with get_db(HISTORY_DB_PATH) as conn:
        rows = conn.execute(_HISTORY_CANDIDATE_SQL).fetchall()
    stats.scanned = len(rows)

    changes: List[Tuple] = []
    for r in rows:
        old_href = r["Href"] or ''
        old_actor = r["ActorLink"] or ''
        old_sup = r["SupportingActors"] or ''
        new_href = javdb_absolute_url(old_href, base_url) if old_href else old_href
        new_actor = javdb_absolute_url(old_actor, base_url) if old_actor else old_actor
        new_sup = (
            absolutize_supporting_actors_json(old_sup, base_url)
            if old_sup else old_sup
        )
        if new_href == old_href and new_actor == old_actor and new_sup == old_sup:
            stats.unchanged += 1
            continue
        changes.append((new_href, new_actor, new_sup, r["Id"]))

    if dry_run:
        stats.updated = len(changes)
        return stats

    # Apply in small chunks; each get_db context commits (flushes D1) on exit.
    for chunk in _chunked(changes, 50):
        with get_db(HISTORY_DB_PATH) as conn:
            for new_href, new_actor, new_sup, row_id in chunk:
                conn.execute(
                    "UPDATE MovieHistory SET Href=?, ActorLink=?, "
                    "SupportingActors=? WHERE Id=?",
                    (new_href, new_actor, new_sup, row_id),
                )
        stats.updated += len(chunk)
    return stats


def _process_reports(base_url: str, dry_run: bool) -> RunStats:
    stats = RunStats()
    with get_db(REPORTS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT Id AS Id, Href AS Href FROM ReportMovies WHERE Href LIKE '/%'"
        ).fetchall()
    stats.scanned = len(rows)

    changes: List[Tuple] = []
    for r in rows:
        old_href = r["Href"] or ''
        new_href = javdb_absolute_url(old_href, base_url) if old_href else old_href
        if new_href == old_href:
            stats.unchanged += 1
            continue
        changes.append((new_href, r["Id"]))

    if dry_run:
        stats.updated = len(changes)
        return stats

    for chunk in _chunked(changes, 50):
        with get_db(REPORTS_DB_PATH) as conn:
            for new_href, row_id in chunk:
                conn.execute(
                    "UPDATE ReportMovies SET Href=? WHERE Id=?",
                    (new_href, row_id),
                )
        stats.updated += len(chunk)
    return stats


def main() -> int:
    p = argparse.ArgumentParser(
        description="Absolutize JavDB URL columns (backend-aware; D1 canonical)."
    )
    p.add_argument(
        "--base-url",
        default=cfg('BASE_URL', 'https://javdb.com'),
        help="Base site URL used for absolute links",
    )
    p.add_argument(
        "--also-reports-db",
        action="store_true",
        help="Also normalize ReportMovies.Href",
    )
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--apply", action="store_true", help="Apply updates")
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview counts only (default)",
    )
    args = p.parse_args()

    base_url = (args.base_url or '').strip() or cfg('BASE_URL', 'https://javdb.com')
    dry_run = not args.apply
    mode_text = "dry-run" if dry_run else "applied"

    h = _process_history(base_url, dry_run)
    logger.info(
        "[%s] MovieHistory: scanned=%d updated=%d unchanged=%d",
        mode_text, h.scanned, h.updated, h.unchanged,
    )

    if args.also_reports_db:
        r = _process_reports(base_url, dry_run)
        logger.info(
            "[%s] ReportMovies: scanned=%d updated=%d unchanged=%d",
            mode_text, r.scanned, r.updated, r.unchanged,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
