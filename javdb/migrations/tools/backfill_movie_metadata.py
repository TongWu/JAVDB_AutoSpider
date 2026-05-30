"""Backfill MovieMetadata for existing MovieHistory rows that lack metadata.

For each ``MovieHistory.Href`` that has no corresponding ``MovieMetadata`` row,
fetch the JavDB detail page and upsert via :class:`MetadataRepo`.

Execution model: **single-threaded / sequential**.  Each page is fetched via
``spider_state.get_page`` which honours the proxy pool when ``use_proxy`` is set
(``--no-proxy`` forces a direct request).  A one-time catch-up job does not need
the spider's parallel-per-proxy machinery, and ``FetchEngine`` exposes no
public result-draining API to reuse here, so a simple sequential loop is both
correct and sufficient.  Writes are OUTSIDE the Pending→Commit session flow --
failures are logged and retriable on the next run.

Usage (via migrate_to_current.py):
    python3 -m apps.cli.db.migration --backfill-metadata --dry-run
    python3 -m apps.cli.db.migration --backfill-metadata \\
        --backfill-metadata-limit 50 --backfill-metadata-shuffle

Direct usage (debugging):
    python3 -m javdb.migrations.tools.backfill_movie_metadata --dry-run --no-proxy --limit 5
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Optional

import requests

from javdb.infra.config import cfg
from javdb.infra.logging import get_logger, setup_logging
from javdb.parsing import parse_detail_page
from javdb.storage.db import get_db, HISTORY_DB_PATH
from javdb.storage.repos.metadata_repo import MetadataRepo
import javdb.spider.runtime.state as spider_state
from javdb.spider.runtime.sleep import movie_sleep_mgr

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_hrefs_without_metadata(
    only_hrefs: Optional[List[str]] = None,
) -> List[str]:
    """Return MovieHistory.Href values that have no MovieMetadata row.

    If *only_hrefs* is given, restrict to that set.

    Returns an empty list (with a warning) when the required tables are not
    present — e.g. a database that has not had the ADR-022 schema migration
    applied yet.  Backfill normally runs *after* schema migration, so a missing
    table means "nothing to backfill here", not a fatal error.
    """
    sql = """
        SELECT mh.Href
        FROM   MovieHistory mh
        LEFT JOIN MovieMetadata mm ON mm.href = mh.Href
        WHERE  mm.href IS NULL
        ORDER  BY mh.DateTimeCreated DESC
    """
    with get_db(HISTORY_DB_PATH) as conn:
        present = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('MovieHistory', 'MovieMetadata')"
            ).fetchall()
        }
        missing = {'MovieHistory', 'MovieMetadata'} - present
        if missing:
            logger.warning(
                "Backfill skipped: required table(s) %s not found in the "
                "history database — run the schema migration first.",
                ", ".join(sorted(missing)),
            )
            return []
        rows = conn.execute(sql).fetchall()
    all_missing = [r[0] for r in rows]

    if only_hrefs:
        only_set = set(only_hrefs)
        return [h for h in all_missing if h in only_set]
    return all_missing


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BackfillResult:
    href: str
    status: str           # 'ok' | 'parse_failed' | 'write_failed' | 'dry_run' | 'fetch_failed'
    message: str = ''


# ---------------------------------------------------------------------------
# Per-href processing
# ---------------------------------------------------------------------------

def _process_href(
    href: str,
    detail_url: str,
    session: requests.Session,
    *,
    use_proxy: bool,
    dry_run: bool,
) -> BackfillResult:
    """Fetch + parse one detail page and (unless dry-run) upsert metadata."""
    try:
        html = spider_state.get_page(
            detail_url, session=session, use_proxy=use_proxy,
            module_name='spider',
        )
    except Exception as exc:  # noqa: BLE001 — fetch errors are recoverable
        return BackfillResult(href, 'fetch_failed', str(exc))
    if not html:
        return BackfillResult(href, 'fetch_failed', 'empty response')

    try:
        detail = parse_detail_page(html)
    except Exception as exc:  # noqa: BLE001
        return BackfillResult(href, 'parse_failed', str(exc))
    if not detail.parse_success:
        return BackfillResult(href, 'parse_failed', 'parse_success=False')

    if dry_run:
        return BackfillResult(href, 'dry_run')

    try:
        MetadataRepo().upsert(href, detail.__dict__)
    except Exception as exc:  # noqa: BLE001 — write failures are retriable
        return BackfillResult(href, 'write_failed', str(exc))
    return BackfillResult(href, 'ok')


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backfill_metadata(args: SimpleNamespace) -> int:
    """Run the MovieMetadata backfill.

    Args:
        args: Namespace with fields:
            dry_run (bool)
            limit (int)            — absolute cap, 0=all
            limit_per_worker (int) — volume cap scaled by proxy-pool size, 0=use limit
            hrefs (str)            — comma-separated href overrides, '' = all
            use_proxy (bool)
            shuffle (bool)

    Returns:
        0 on success (or nothing to do), 1 on partial failure.
    """
    only_hrefs: Optional[List[str]] = None
    if args.hrefs:
        only_hrefs = [h.strip() for h in args.hrefs.split(',') if h.strip()]

    hrefs = _load_hrefs_without_metadata(only_hrefs)

    if args.shuffle:
        random.shuffle(hrefs)

    limit = int(getattr(args, 'limit', 0) or 0)
    limit_per_worker = int(getattr(args, 'limit_per_worker', 0) or 0)
    use_proxy = getattr(args, 'use_proxy', True)

    # ``--limit-per-worker`` predates the switch to sequential execution; it is
    # kept for workflow-input compatibility and interpreted against the
    # configured proxy-pool size so the same input caps a comparable volume.
    if limit_per_worker > 0:
        from javdb.spider.runtime.config import PROXY_POOL
        num_workers = len(PROXY_POOL) if (use_proxy and PROXY_POOL) else 1
        hrefs = hrefs[: limit_per_worker * num_workers]
    elif limit > 0:
        hrefs = hrefs[:limit]

    total = len(hrefs)
    logger.info(
        "MovieMetadata backfill: %d hrefs to process%s",
        total,
        " (dry-run)" if args.dry_run else "",
    )
    if total == 0:
        logger.info(
            "Nothing to backfill — all MovieHistory rows already have metadata."
        )
        return 0

    spider_state.setup_proxy_pool(use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    base_url = cfg('BASE_URL', 'https://javdb.com').rstrip('/')
    session = requests.Session()

    ok = failed = 0
    for i, href in enumerate(hrefs, 1):
        idx = f"meta-{i}/{total}"
        result = _process_href(
            href, base_url + href, session,
            use_proxy=use_proxy, dry_run=args.dry_run,
        )
        if result.status in ('ok', 'dry_run'):
            logger.info("[%s] ✓ %s", idx, href)
            ok += 1
        else:
            logger.warning(
                "[%s] %s — %s: %s", idx, href, result.status, result.message
            )
            failed += 1
        if i < total:
            time.sleep(
                random.uniform(movie_sleep_mgr.base_min, movie_sleep_mgr.base_max)
            )

    logger.info(
        "MovieMetadata backfill complete: %d ok, %d failed out of %d",
        ok, failed, total,
    )
    return 0 if failed == 0 else 1


def parse_args() -> SimpleNamespace:
    parser = argparse.ArgumentParser(
        description="Backfill MovieMetadata for MovieHistory rows that lack metadata."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but do not write to DB.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max hrefs to process in total (0=all). "
                             "Ignored when --limit-per-worker > 0.")
    parser.add_argument("--limit-per-worker", type=int, default=0,
                        dest="limit_per_worker",
                        help="Volume cap scaled by proxy-pool size "
                             "(0=use --limit or all).")
    parser.add_argument("--hrefs", type=str, default='',
                        help="Comma-separated movie hrefs to process "
                             "(default: all missing).")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Direct HTTP without proxy (debug).")
    parser.add_argument("--shuffle", action="store_true",
                        help="Randomise processing order.")
    args = parser.parse_args()
    return SimpleNamespace(
        dry_run=args.dry_run,
        limit=args.limit,
        limit_per_worker=args.limit_per_worker,
        hrefs=args.hrefs,
        use_proxy=not args.no_proxy,
        shuffle=args.shuffle,
    )


if __name__ == "__main__":
    raise SystemExit(run_backfill_metadata(parse_args()))
