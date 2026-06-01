"""Backfill MovieMetadata for existing MovieHistory rows that lack metadata.

For each ``MovieHistory.Href`` that has no corresponding ``MovieMetadata`` row,
fetch the JavDB detail page and upsert via :class:`MetadataRepo`.

Execution model: proxy-enabled runs use ``FetchEngine.simple`` with one worker
per configured proxy so failed/empty detail pages can be re-queued to another
proxy. ``--no-proxy`` and no-pool runs keep the single-threaded debug path via
``spider_state.get_page``.  Detail pages sit behind Cloudflare, so proxied
fetches use the engine's direct→CF-bypass cascade; plain direct fetches may
still return an empty body.  Writes stay in the main thread and are OUTSIDE the
Pending→Commit session flow -- failures are logged and retriable on the next
run.

Fetches are authenticated (``use_cookie=True`` attaches ``JAVDB_SESSION_COOKIE``,
like the ad-hoc spider) so login-gated movies yield metadata rather than a
login wall.  Because the bare ``get_page`` path has no ``LoginRequired``
machinery, a login wall (missing/expired cookie) is detected explicitly via
``is_login_page`` and reported as ``login_required`` — distinct from a genuine
``parse_failed`` — without failing the job.

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
from javdb.infra.logging import get_logger, log_summary_block, setup_logging
from javdb.parsing import parse_detail_page
from javdb.spider.html_validators import is_login_page
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
    # Alias every selected column and read rows by key: under
    # STORAGE_BACKEND=d1/dual (the canonical backend the backfill runs on in
    # CI) ``get_db`` returns dict-shaped rows, so positional ``r[0]`` access
    # would raise KeyError. Key access works for both sqlite3.Row and D1 dicts.
    sql = """
        SELECT mh.Href AS Href
        FROM   MovieHistory mh
        LEFT JOIN MovieMetadata mm ON mm.href = mh.Href
        WHERE  mm.href IS NULL
        ORDER  BY mh.DateTimeCreated DESC
    """
    with get_db(HISTORY_DB_PATH) as conn:
        present = {
            r["name"] for r in conn.execute(
                "SELECT name AS name FROM sqlite_master WHERE type='table' "
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
    all_missing = [r["Href"] for r in rows]

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
    # 'ok' | 'dry_run' | 'fetch_failed' | 'login_required' | 'parse_failed' | 'write_failed'
    status: str
    message: str = ''


# ---------------------------------------------------------------------------
# URL helper
# ---------------------------------------------------------------------------

def _detail_url(href: str, base_url: str) -> str:
    """Resolve a ``MovieHistory.Href`` to an absolute detail URL.

    ``Href`` is stored as an absolute URL (``https://javdb.com/v/..``), so it
    is returned verbatim.  Only the legacy/relative ``/v/..`` form has
    *base_url* prepended — prepending it to an already-absolute href yields a
    doubled ``https://..https://..`` URL that never resolves (every fetch then
    fails with an empty response).
    """
    if href.startswith(("http://", "https://")):
        return href
    return base_url + "/" + href.lstrip("/")


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
    # JavDB detail pages sit behind Cloudflare: a plain direct fetch returns
    # an empty/challenge body, so a no-bypass request fails on every proxy.
    # Route through the proxy's CF-bypass service (use_cf_bypass), which runs
    # the full bypass→direct fallback sequence internally — the same path the
    # spider relies on for detail pages. CF bypass is only meaningful when a
    # proxy is in play; the --no-proxy debug path has no local bypass service,
    # so it stays a direct request.
    #
    # use_cookie=True attaches the configured JAVDB_SESSION_COOKIE (like the
    # ad-hoc spider), so login-gated movies render their metadata instead of a
    # login wall. The cookie only attaches when configured (request handler
    # guards on it), so an unconfigured/empty cookie degrades to an
    # unauthenticated fetch — which is_login_page() below then flags as
    # 'login_required' rather than misreporting 'parse_failed'.
    try:
        html = spider_state.get_page(
            detail_url, session=session, use_proxy=use_proxy,
            module_name='spider', use_cf_bypass=use_proxy, use_cookie=True,
        )
    except Exception as exc:  # noqa: BLE001 — fetch errors are recoverable
        return BackfillResult(href, 'fetch_failed', str(exc))
    if not html:
        return BackfillResult(href, 'fetch_failed', 'empty response')

    # Distinguish a login wall (cookie missing/expired, or content login-gated)
    # from a genuine parse failure: the bare get_page path has no LoginRequired
    # machinery, so detect it explicitly here. Best-effort — the proxied
    # CF-bypass path swallows a small login page to None before returning (the
    # request handler's "Last response appears to be a login page" branch), so
    # there it surfaces as 'fetch_failed' above, not here. This fires when the
    # login HTML actually reaches us (the --no-proxy direct path, or a login
    # page large enough to clear the CF-bypass size gate). The primary fix is
    # use_cookie=True above, which avoids the wall entirely when the cookie is
    # valid.
    if is_login_page(html):
        return BackfillResult(
            href, 'login_required',
            'session cookie missing or expired — refresh JAVDB_SESSION_COOKIE',
        )

    try:
        detail = parse_detail_page(html)
    except Exception as exc:  # noqa: BLE001
        return BackfillResult(href, 'parse_failed', str(exc))
    # detail.parse_success reflects ONLY whether a #magnets-content section was
    # found — magnets are frequently login-gated and irrelevant to a metadata
    # backfill. Metadata fields (title / video_code / release_date / rate /
    # maker / …) are parsed independently, so accept the page whenever core
    # metadata was extracted and reject only genuinely empty pages (Cloudflare
    # challenge, deleted movie, or a body with no detail panel).
    if not (getattr(detail, 'video_code', '') or getattr(detail, 'title', '')):
        return BackfillResult(href, 'parse_failed', 'no metadata fields parsed')

    if dry_run:
        return BackfillResult(href, 'dry_run')

    try:
        MetadataRepo().upsert(href, detail)
    except Exception as exc:  # noqa: BLE001 — write failures are retriable
        return BackfillResult(href, 'write_failed', str(exc))
    return BackfillResult(href, 'ok')


# ---------------------------------------------------------------------------
# Parallel result helpers
# ---------------------------------------------------------------------------

def _backfill_metadata_parse(html: str, _task) -> object | None:
    """Parse detail HTML for ``FetchEngine.simple``.

    Return ``None`` only for genuinely empty/non-detail pages so the engine can
    re-queue the task to another proxy. ``parse_success`` is magnet-specific and
    intentionally ignored here.
    """
    detail = parse_detail_page(html)
    if getattr(detail, 'video_code', '') or getattr(detail, 'title', ''):
        return detail
    return None


def _apply_metadata_result(result, *, dry_run: bool) -> tuple[int, int]:
    """Apply one ``FetchEngine`` result on the main thread.

    Returns ``(ok_delta, failed_delta)`` for the summary counters.
    """
    task = result.task
    idx = getattr(task, 'entry_index', '') or 'meta-?'
    href = task.meta.get('href') or getattr(task, 'url', '')

    if not result.success:
        from javdb.spider.fetch.fetch_engine import PER_WORKER_TASK_CAP_ERROR

        if result.error == PER_WORKER_TASK_CAP_ERROR:
            logger.info(
                "[%s] %s skipped — queue flushed after per-worker task cap",
                idx,
                href,
            )
            return 0, 0
        # In parallel mode FetchEngine owns LoginRequired handling. An
        # uncleared login wall intentionally surfaces as a generic retriable
        # failure; the separate login-gated counter is fallback-only (ADR-045 D3).
        logger.warning(
            "[%s] %s — fetch_failed: %s",
            idx,
            href,
            result.error or 'all proxies failed',
        )
        return 0, 1

    if dry_run:
        logger.info("[%s] ✓ %s (dry-run)", idx, href)
        return 1, 0

    try:
        MetadataRepo().upsert(href, result.data)
    except Exception as exc:  # noqa: BLE001 — write failures are retriable
        logger.warning("[%s] %s — write_failed: %s", idx, href, exc)
        return 0, 1

    logger.info("[%s] ✓ %s", idx, href)
    return 1, 0


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
        0 when the batch succeeds, has nothing to do, or makes partial
        progress; 1 when every attempted href hard-fails.
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
    from javdb.spider.runtime.config import PROXY_POOL
    parallel_mode = bool(use_proxy and PROXY_POOL)

    if parallel_mode:
        if limit > 0:
            hrefs = hrefs[:limit]
    elif limit_per_worker > 0:
        # ``--limit-per-worker`` is primarily a proxy-backed engine cap; in
        # the fallback path it keeps the old precedence and is interpreted
        # against the effective worker count so workflow-input volume stays
        # comparable to earlier runs.
        num_workers = 1
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

    ok = failed = login_gated = 0
    if parallel_mode:
        from javdb.spider.fetch.fetch_engine import FetchEngine

        movie_sleep_mgr.apply_volume_multiplier(
            total, num_workers=len(PROXY_POOL),
        )
        engine = FetchEngine.simple(
            parse_fn=_backfill_metadata_parse,
            use_cookie=True,
            sleep_min=movie_sleep_mgr.base_min,
            sleep_max=movie_sleep_mgr.base_max,
            per_worker_task_limit=(
                limit_per_worker if limit_per_worker > 0 else 0
            ),
        )
        parallel_interrupted = False
        orphaned_count = 0
        try:
            engine.start()
            for i, href in enumerate(hrefs, 1):
                engine.submit(
                    _detail_url(href, base_url),
                    entry_index=f"meta-{i}/{total}",
                    meta={'href': href},
                )
            engine.mark_done()
            for result in engine.results():
                ok_delta, failed_delta = _apply_metadata_result(
                    result, dry_run=args.dry_run,
                )
                ok += ok_delta
                failed += failed_delta
        except KeyboardInterrupt:
            parallel_interrupted = True
            logger.warning("Keyboard interrupt — shutting down engine …")
            orphaned = engine.shutdown(timeout=30)
            orphaned_count = len(orphaned)
            for result in engine.drain_remaining():
                ok_delta, failed_delta = _apply_metadata_result(
                    result, dry_run=args.dry_run,
                )
                ok += ok_delta
                failed += failed_delta
        else:
            engine.shutdown()

        if parallel_interrupted:
            logger.warning(
                "MovieMetadata backfill interrupted (parallel, %d workers). "
                "%d task(s) orphaned; re-run backfill to continue.",
                len(PROXY_POOL),
                orphaned_count,
            )
    else:
        session = requests.Session()

        for i, href in enumerate(hrefs, 1):
            idx = f"meta-{i}/{total}"
            result = _process_href(
                href, _detail_url(href, base_url), session,
                use_proxy=use_proxy, dry_run=args.dry_run,
            )
            if result.status in ('ok', 'dry_run'):
                logger.info("[%s] ✓ %s", idx, href)
                ok += 1
            elif result.status == 'login_required':
                # Not a hard failure: the page exists but needs a valid session
                # cookie. Counted separately so it doesn't fail the job, but
                # surfaced so the operator knows to refresh the cookie.
                logger.warning(
                    "[%s] %s — login_required: %s", idx, href, result.message
                )
                login_gated += 1
            else:
                logger.warning(
                    "[%s] %s — %s: %s", idx, href, result.status, result.message
                )
                failed += 1
            if i < total:
                # Intentional non-cryptographic jitter for crawl timing (anti-ban).
                time.sleep(
                    random.uniform(  # noqa: S311
                        movie_sleep_mgr.base_min, movie_sleep_mgr.base_max
                    )
                )

    log_summary_block(logger, "MovieMetadata Backfill", {
        "OK": ok,
        "Failed": failed,
        "Login-gated": login_gated,
        "Total": total,
    })
    if login_gated:
        logger.warning(
            "%d href(s) require login — refresh JAVDB_SESSION_COOKIE "
            "(run `python3 -m apps.cli.login`) and re-run to backfill them.",
            login_gated,
        )
    if parallel_mode and parallel_interrupted:
        return 130
    if failed == 0:
        return 0
    if ok > 0:
        logger.warning(
            "MovieMetadata backfill completed with %d success(es) and %d "
            "failure(s); continuing because partial progress was made.",
            ok,
            failed,
        )
        return 0
    return 1


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
