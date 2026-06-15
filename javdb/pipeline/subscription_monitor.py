"""Subscription monitor: scrape followed actors, write the new-works feed.

ADR-054 WS2 reuses the AdHoc spider path (``apps.cli.spider --url``), whose
index selection is tag-based in adhoc mode and does not apply the ADR-040
rating threshold. The monitor adds no new rating-bypass branch.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Iterable, List, Optional, Set

from javdb.infra.config import cfg
from javdb.parsing.common import javdb_absolute_url, normalize_javdb_href_path
from javdb.spider.app.result import read_spider_result
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)
from javdb.storage.sessions.commit import CommitRequest, commit_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapedWork:
    """One release observed for a followed actor."""

    video_code: str
    href: str
    title: Optional[str] = None
    release_date: Optional[str] = None


def actor_url(actor_href: str) -> str:
    """Build the full JavDB actor URL from a normalized /actors/<id> href."""
    base_url = cfg("BASE_URL", "https://javdb.com")
    return javdb_absolute_url(actor_href, base_url)


def commit_spider_session(session_id: str | None) -> None:
    """Promote the spider session's pending history writes before diffing."""
    if not session_id:
        raise RuntimeError(
            "Spider result did not include a session_id; cannot commit "
            "subscription scrape before diffing MovieHistory.",
        )
    result = commit_session(CommitRequest(session_id=session_id, fanout_claims=True))
    logger.info(
        "Committed subscription scrape session %s (state=%s)",
        result.session_id,
        result.new_state,
    )


def scrape_actor(actor_href: str, *, use_proxy: bool = False) -> str | None:
    """Run the full AdHoc spider for a followed actor.

    This is intentionally the existing ``--url`` entrypoint. In index selection,
    custom URLs set ``is_adhoc_mode=True``, so phase-2 rating thresholds are
    bypassed by construction.
    """
    with TemporaryDirectory(prefix="subscription-spider-") as tmpdir:
        result_json = f"{tmpdir}/spider-result.json"
        cmd = [
            sys.executable,
            "-m",
            "apps.cli.spider",
            "--url",
            actor_url(actor_href),
            "--result-json",
            result_json,
        ]
        if use_proxy:
            cmd.append("--use-proxy")
        logger.info("Scraping followed actor %s via AdHoc spider path", actor_href)
        subprocess.run(cmd, check=True)
        result = read_spider_result(result_json)
        return result.session_id


def load_seen_video_codes(actor_href: str, *, db_path: Optional[str] = None) -> Set[str]:
    """Video codes already ingested for this actor before a scrape starts."""
    return {
        work.video_code
        for work in load_actor_works_from_history(actor_href, db_path=db_path)
        if work.video_code
    }


def load_actor_works_from_history(
    actor_href: str, *, db_path: Optional[str] = None
) -> List[ScrapedWork]:
    """Read this actor's ingested works back out of MovieHistory, newest first."""
    path = db_path or _db.HISTORY_DB_PATH
    href = normalize_javdb_href_path(actor_href) or actor_href
    base_url = cfg("BASE_URL", "https://javdb.com")
    actor_keys = [href, javdb_absolute_url(href, base_url)]
    actor_placeholders = ", ".join("?" for _ in actor_keys)
    base = base_url.rstrip("/")

    with get_db(path) as conn:
        rows = conn.execute(
            f"SELECT "  # noqa: S608
            "m.VideoCode, "
            "m.Href, "
            "COALESCE("
            "(SELECT mm.title FROM MovieMetadata AS mm "
            "WHERE mm.href = m.Href LIMIT 1), "
            "(SELECT mm.title FROM MovieMetadata AS mm "
            "WHERE mm.href = CASE WHEN m.Href LIKE '/%' THEN ? || m.Href ELSE '' END "
            "LIMIT 1), "
            "(SELECT mm.title FROM MovieMetadata AS mm "
            "WHERE mm.href LIKE '/%' AND ? || mm.href = m.Href LIMIT 1)"
            ") AS title, "
            "COALESCE("
            "(SELECT mm.release_date FROM MovieMetadata AS mm "
            "WHERE mm.href = m.Href LIMIT 1), "
            "(SELECT mm.release_date FROM MovieMetadata AS mm "
            "WHERE mm.href = CASE WHEN m.Href LIKE '/%' THEN ? || m.Href ELSE '' END "
            "LIMIT 1), "
            "(SELECT mm.release_date FROM MovieMetadata AS mm "
            "WHERE mm.href LIKE '/%' AND ? || mm.href = m.Href LIMIT 1)"
            ") AS release_date "
            "FROM MovieHistory AS m "
            f"WHERE m.ActorLink IN ({actor_placeholders}) "
            "OR EXISTS ("
            "SELECT 1 FROM json_each("
            "CASE WHEN json_valid(m.SupportingActors) "
            "THEN m.SupportingActors ELSE '[]' END"
            ") AS actor "
            f"WHERE json_extract(actor.value, '$.link') IN ({actor_placeholders}) "
            f"OR json_extract(actor.value, '$.href') IN ({actor_placeholders})"
            ") "
            "ORDER BY m.DateTimeCreated DESC, m.Id DESC",
            (
                base,
                base,
                base,
                base,
                *actor_keys,
                *actor_keys,
                *actor_keys,
            ),
        ).fetchall()

    works: list[ScrapedWork] = []
    for row in rows:
        code = row["VideoCode"]
        href_value = normalize_javdb_href_path(row["Href"]) or row["Href"]
        if code and href_value:
            works.append(
                ScrapedWork(
                    video_code=code,
                    href=href_value,
                    title=row["title"],
                    release_date=row["release_date"],
                )
            )
    return works


def process_actor(
    *,
    actor_href: str,
    scraped: Iterable[ScrapedWork],
    seen_video_codes: Set[str],
    subs_repo: ActorSubscriptionRepo,
    new_works_repo: NewWorksRepo,
) -> int:
    """Diff a fresh actor scrape against the seen-set and cursor.

    ``seen_video_codes`` is captured before the scrape. ``last_seen_href`` acts
    as the per-subscription cursor: once the current scrape reaches it, older
    entries are ignored.
    """
    scraped_list = list(scraped)
    sub = subs_repo.get(actor_href) or {}
    cursor_href = normalize_javdb_href_path(sub.get("last_seen_href") or "")

    added = 0
    for work in scraped_list:
        work_href = normalize_javdb_href_path(work.href) or work.href
        if cursor_href and work_href == cursor_href:
            break
        if work.video_code in seen_video_codes:
            continue
        if new_works_repo.add(
            video_code=work.video_code,
            href=work_href,
            actor_href=actor_href,
            title=work.title,
            release_date=work.release_date,
        ):
            added += 1

    newest_href = scraped_list[0].href if scraped_list else None
    subs_repo.advance_cursor(actor_href, last_seen_href=newest_href)
    logger.info(
        "Actor %s: %d new work(s) added to feed (scraped %d)",
        actor_href,
        added,
        len(scraped_list),
    )
    return added


def run_subscription_monitor(
    *, use_proxy: bool = False, db_path: Optional[str] = None
) -> int:
    """Scrape every active subscription and return total new feed rows added."""
    subs_repo = ActorSubscriptionRepo(db_path=db_path)
    new_works_repo = NewWorksRepo(db_path=db_path)
    actor_hrefs = subs_repo.list_active_hrefs()
    if not actor_hrefs:
        logger.info("No active subscriptions - nothing to scrape.")
        return 0

    total_added = 0
    for raw_href in actor_hrefs:
        actor_href = normalize_javdb_href_path(raw_href) or raw_href
        seen_before = load_seen_video_codes(actor_href, db_path=db_path)
        try:
            session_id = scrape_actor(actor_href, use_proxy=use_proxy)
            commit_spider_session(session_id)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Scrape failed for %s (exit %s); skipping",
                actor_href,
                exc.returncode,
            )
            continue
        except Exception:
            logger.exception(
                "Post-scrape commit failed for %s; failing monitor.",
                actor_href,
            )
            raise
        scraped = load_actor_works_from_history(actor_href, db_path=db_path)
        total_added += process_actor(
            actor_href=actor_href,
            scraped=scraped,
            seen_video_codes=seen_before,
            subs_repo=subs_repo,
            new_works_repo=new_works_repo,
        )

    logger.info("Subscription monitor complete: %d new feed row(s).", total_added)
    return total_added
