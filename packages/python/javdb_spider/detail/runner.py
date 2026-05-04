"""Shared runner helpers for spider detail-page orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.config_helper import use_sqlite
from packages.python.javdb_platform.db import db_batch_update_movie_actors
from packages.python.javdb_platform.history_manager import (
    save_parsed_movie_to_history,
    batch_update_last_visited,
)
from packages.python.javdb_platform.csv_writer import write_csv
from packages.python.javdb_platform.movie_claim_client import (
    DEFAULT_CLAIM_TTL_MS,
    MovieClaimUnavailable,
    current_shard_date,
)
from packages.python.javdb_core.magnet_extractor import extract_magnets

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_ingestion.models import ParsedMovie
from packages.python.javdb_ingestion.planner import build_spider_ingestion_plan
from packages.python.javdb_ingestion.policies import (
    has_complete_subtitles,
    should_skip_recent_today_release,
    should_skip_recent_yesterday_release,
)
from packages.python.javdb_spider.services.dedup import (
    should_skip_from_rclone,
    append_dedup_record,
)
from packages.python.javdb_spider.fetch.backend import FetchBackend
from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
from packages.python.javdb_spider.runtime.config import BASE_URL

logger = get_logger(__name__)


def _claim_detail_candidates(
    candidates: List["DetailEntryCandidate"],
) -> Tuple[List["DetailEntryCandidate"], int, int, Optional[str], Set[str]]:
    """Acquire MovieClaim leases for *candidates* before submitting fetches.

    P1-B integration point: when ``state.global_movie_claim_client`` is
    configured, every candidate is run through ``claim_movie`` so peer
    runners observe one of the three exhaustive outcomes from
    :class:`ClaimResult`:

    1. ``acquired=True`` → keep the candidate; it will be fetched by this
       runner and ``complete``/``release`` will be called from the result
       loop.  The href is added to ``leased_hrefs`` so the result loop
       knows to issue the symmetric ``complete``/``release``.
    2. ``acquired=False, already_completed=True`` → another runner has
       already finished the href in this shard.  Drop locally, count as
       skipped (so the phase report stays accurate) and add to
       :data:`state.parsed_links` to short-circuit subsequent same-run
       calls.
    3. ``acquired=False, already_completed=False`` → another runner is
       *currently* working on it.  Drop locally and skip (the back-off is
       implicit: if the peer runner fails / releases, this runner will
       see the href again on the next ingestion pass).

    On any :class:`MovieClaimUnavailable`, on a missing client, or on any
    unexpected exception, the candidate is **kept but NOT added to
    ``leased_hrefs``** — fail-open keeps the spider's pre-P1-B behaviour
    (worst case: two runners independently fetch the same detail page,
    exactly as today) and prevents spurious ``complete``/``release``
    calls for hrefs we never actually leased.

    Returns:
        ``(kept, skipped_already_completed, skipped_contention,
        shard_date, leased_hrefs)`` where ``shard_date`` is the per-day
        key the claims were registered under (or ``None`` when no
        client was active), and ``leased_hrefs`` is the subset of kept
        hrefs that returned ``acquired=True``.  The same ``shard_date``
        MUST be passed back to ``complete``/``release`` for symmetric ops.
    """
    client = state.global_movie_claim_client
    if client is None or not candidates:
        return list(candidates), 0, 0, None, set()

    shard_date = current_shard_date()
    holder = state.runtime_holder_id
    kept: List["DetailEntryCandidate"] = []
    leased: Set[str] = set()
    skipped_completed = 0
    skipped_contention = 0
    for candidate in candidates:
        try:
            result = client.claim(
                candidate.href,
                holder,
                ttl_ms=DEFAULT_CLAIM_TTL_MS,
                date=shard_date,
            )
        except MovieClaimUnavailable:
            # Fail-open: surface as an INFO so ops can correlate, but
            # never block the candidate.  Do NOT add to ``leased`` —
            # we have no DO lease to release/complete later.
            logger.info(
                "MovieClaim unavailable for %s — falling back to local dedup",
                candidate.href,
            )
            kept.append(candidate)
            continue
        except Exception:  # noqa: BLE001 — claim is never allowed to break the run
            logger.warning(
                "Unexpected MovieClaim error for %s — keeping candidate",
                candidate.href, exc_info=True,
            )
            kept.append(candidate)
            continue

        if result.acquired:
            kept.append(candidate)
            leased.add(candidate.href)
            continue
        if result.already_completed:
            logger.info(
                "[%s] Skipping %s — already completed by peer runner in shard %s",
                candidate.entry_index, candidate.entry.get("video_code") or candidate.href,
                shard_date,
            )
            skipped_completed += 1
            state.parsed_links.add(candidate.href)
            continue
        # acquired=False, already_completed=False → live contention
        logger.info(
            "[%s] Skipping %s — currently held by %s in shard %s",
            candidate.entry_index,
            candidate.entry.get("video_code") or candidate.href,
            result.current_holder_id or "<unknown>",
            shard_date,
        )
        skipped_contention += 1

    return kept, skipped_completed, skipped_contention, shard_date, leased


def _release_movie_claim(href: str, shard_date: Optional[str]) -> None:
    """Best-effort release of a MovieClaim lease.  Never raises."""
    client = state.global_movie_claim_client
    if client is None or shard_date is None or not href:
        return
    try:
        client.release(href, state.runtime_holder_id, date=shard_date)
    except MovieClaimUnavailable:
        logger.debug("MovieClaim release unavailable for %s — ignoring", href)
    except Exception:  # noqa: BLE001
        logger.warning("Unexpected MovieClaim release error for %s", href, exc_info=True)


def _complete_movie_claim(href: str, shard_date: Optional[str]) -> bool:
    """Best-effort completion of a MovieClaim lease.  Never raises.

    Returns ``True`` only when the coordinator explicitly confirmed
    completion (``CompleteResult.completed=True``); ``False`` on any
    error path (``MovieClaimUnavailable``, malformed response,
    unexpected exception) **or** when the Worker returned
    ``completed=False`` — that latter case happens when the active
    claim has already been re-leased by another runner (typically
    after our TTL expired or a stale-holder mismatch).  In every
    ``False`` branch the lease is still attributed to this runner on
    the Worker side, so the caller MUST follow up with
    :func:`_release_movie_claim` before dropping the href from
    ``held_claims``; otherwise peer runners stay blocked until the
    default 30-minute TTL expires.
    """
    client = state.global_movie_claim_client
    if client is None or shard_date is None or not href:
        return False
    try:
        result = client.complete(href, state.runtime_holder_id, date=shard_date)
    except MovieClaimUnavailable:
        logger.debug(
            "MovieClaim complete unavailable for %s — falling back to release",
            href,
        )
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "Unexpected MovieClaim complete error for %s — falling back to release",
            href, exc_info=True,
        )
        return False
    return bool(getattr(result, "completed", False))


def _classify_fetch_error_kind(error: Optional[str]) -> str:
    """Map a fetch-engine error string to a coarse cooldown taxonomy.

    The Worker's cooldown ladder is purely a function of ``fail_count`` —
    ``error_kind`` is diagnostic (surfaced in ``StatusResult`` and op
    logs) so we only need a short, stable string.  Keeping this list
    short avoids unbounded cardinality in DO storage and gives ops a
    handful of buckets they can grep in logs.
    """
    if not error:
        return "unknown"
    e = error.lower()
    if "timeout" in e or "timed out" in e:
        return "timeout"
    if "login" in e or "auth" in e:
        return "login_required"
    if "cf" in e or "cloudflare" in e or "challenge" in e:
        return "cf_bypass"
    if "proxy" in e:
        return "proxy_error"
    if "404" in e or "not found" in e:
        return "not_found"
    return "fetch_error"


def _report_movie_claim_failure(
    href: str,
    shard_date: Optional[str],
    *,
    error_kind: str = "",
) -> bool:
    """Best-effort P2-A failure report for a MovieClaim lease.

    On success the DO bumps ``fail_count`` and releases the active claim
    (so peer runners see the slot as free without an extra
    :func:`_release_movie_claim` call).  On any error path — client not
    configured, DO unavailable, malformed response, etc. — we still want
    the lease to be released; the caller arranges that by calling
    :func:`_release_movie_claim` as the fallback in those branches.

    Returns ``True`` when ``report_failure`` succeeded (and therefore
    the DO already released the lease); ``False`` when the caller
    should fall back to a plain ``release`` call.  Never raises.
    """
    client = state.global_movie_claim_client
    if client is None or shard_date is None or not href:
        return False
    try:
        client.report_failure(
            href,
            state.runtime_holder_id,
            error_kind=error_kind or "",
            date=shard_date,
        )
        return True
    except MovieClaimUnavailable:
        logger.debug(
            "MovieClaim report_failure unavailable for %s — falling back to release",
            href,
        )
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "Unexpected MovieClaim report_failure error for %s — falling back to release",
            href, exc_info=True,
        )
        return False


@dataclass(frozen=True)
class DetailEntryCandidate:
    """A detail-page entry that passed pre-fetch filtering."""

    entry: dict
    href: str
    page_num: int
    entry_index: str


@dataclass
class DetailPersistOutcome:
    """Result of persisting one parsed detail page."""

    status: str
    skipped_history: int = 0
    no_new_torrents: int = 0
    row: Optional[dict] = None
    visited_href: Optional[str] = None
    actor_update: Optional[Tuple[str, str, str, str, str]] = None


def process_detail_entries(
    *,
    backend: FetchBackend,
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
    include_recent_release_filters: bool = False,
    log_duplicate_skips: bool = False,
) -> dict:
    """Run the shared detail pipeline against a concrete fetch backend."""

    total_entries = len(entries)

    prepared_entries, skipped_history = prepare_detail_entries(
        entries,
        history_data=history_data,
        is_adhoc_mode=is_adhoc_mode,
        rclone_inventory=rclone_inventory,
        rclone_filter=rclone_filter,
        enable_dedup=enable_dedup,
        enable_redownload=enable_redownload,
        include_recent_release_filters=include_recent_release_filters,
        log_duplicate_skips=log_duplicate_skips,
    )

    # P1-B: filter through the cross-runner MovieClaim mutex.  Returns the
    # candidates this runner won the lease on; peer-completed and
    # peer-contended hrefs are dropped.  Pinned ``shard_date`` MUST be
    # carried into ``complete``/``release`` to avoid re-fragmenting across
    # midnight (see :func:`movie_claim_client.current_shard_date`).
    (
        prepared_entries,
        skipped_completed,
        skipped_contention,
        shard_date,
        leased_hrefs,
    ) = _claim_detail_candidates(prepared_entries)
    skipped_history += skipped_completed
    if skipped_contention:
        logger.info(
            f"Phase {phase}: {skipped_contention} detail tasks deferred "
            "(currently held by other runners)"
        )
    # Track which hrefs still hold an active lease so we can release them
    # on early exit / unexpected shutdown.  Only hrefs that actually
    # returned ``acquired=True`` enter this set — fail-open candidates
    # (claim raised Unavailable) are NOT included since we never received
    # a lease to release.
    held_claims: Set[str] = set(leased_hrefs)

    for candidate in prepared_entries:
        detail_url = urljoin(BASE_URL, candidate.href)
        logger.debug(
            f"[{candidate.entry_index}] [Page {candidate.page_num}] "
            f"Queued {candidate.entry.get('video_code') or candidate.href}"
        )
        backend.submit_task(
            EngineTask(
                url=detail_url,
                entry_index=candidate.entry_index,
                meta={
                    'entry': candidate.entry,
                    'phase': phase,
                    'video_code': candidate.entry.get('video_code', ''),
                },
            )
        )

    runtime_state = backend.runtime_state()
    if not prepared_entries:
        logger.info(f"Phase {phase}: No detail tasks to process (all filtered)")
        return {
            'rows': [],
            'skipped_history': skipped_history,
            'failed': 0,
            'failed_movies': [],
            'no_new_torrents': 0,
            'use_proxy': runtime_state.use_proxy,
            'use_cf_bypass': runtime_state.use_cf_bypass,
        }

    backend.start()
    backend.mark_done()

    logger.info(
        f"Phase {phase}: Started {backend.worker_count} workers for "
        f"{len(prepared_entries)} detail tasks ({skipped_history} skipped by history)"
    )

    phase_rows: list = []
    visited_hrefs: set = set()
    actor_updates: List[tuple] = []
    failed = 0
    failed_movies: list = []
    no_new_torrents = 0
    previous_runtime_state = runtime_state

    try:
        for result in backend.results():
            entry = result.task.meta['entry']
            href = entry['href']
            page_num = entry['page']
            idx_str = result.task.entry_index

            worker_tag = f"[{result.worker_name}] " if result.worker_name else ""

            if not result.success:
                detail_url = urljoin(BASE_URL, href)
                logger.error(
                    f"[{idx_str}] {worker_tag}[Page {page_num}] Failed: "
                    f"{entry.get('video_code', '?')} ({detail_url})"
                )
                failed += 1
                failed_movies.append(
                    {
                        'video_code': entry.get('video_code', '?'),
                        'url': detail_url,
                        'phase': phase,
                    }
                )
                # P2-A: report the failure so the DO bumps fail_count and
                # computes the next cooldown_until (peer runners are then
                # blocked from retrying the same href until the cooldown
                # expires).  ``report_failure`` also releases the active
                # claim on the Worker side, so we do NOT need a paired
                # ``release`` call when it succeeds.  On Unavailable or
                # legacy Workers, fall back to a plain release so the
                # slot still frees up promptly (P1-B parity).
                if href in held_claims:
                    error_kind = _classify_fetch_error_kind(result.error)
                    if not _report_movie_claim_failure(
                        href, shard_date, error_kind=error_kind
                    ):
                        _release_movie_claim(href, shard_date)
                    held_claims.discard(href)
                current_runtime_state = backend.runtime_state()
                result.acknowledge(
                    'failed',
                    runtime_state_changed=(
                        current_runtime_state != previous_runtime_state
                    ),
                )
                previous_runtime_state = current_runtime_state
                continue

            cf_tag = " +CF" if result.used_cf else ""
            logger.info(f"[{idx_str}] {worker_tag}Parsed {entry.get('video_code', '')}{cf_tag}")

            data = result.data or {}
            magnet_links = extract_magnets(data['magnets'], idx_str)
            outcome = persist_parsed_detail_result(
                entry=entry,
                phase=phase,
                entry_index=idx_str,
                worker_name=result.worker_name,
                history_data=history_data,
                history_file=history_file,
                csv_path=csv_path,
                fieldnames=fieldnames,
                dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                is_adhoc_mode=is_adhoc_mode,
                rclone_inventory=rclone_inventory,
                enable_dedup=enable_dedup,
                dedup_csv_path=dedup_csv_path,
                enable_redownload=enable_redownload,
                redownload_threshold=redownload_threshold,
                actor_info=data['actor_info'],
                actor_gender=data['actor_gender'],
                actor_link=data['actor_link'],
                supporting_actors=data['supporting'],
                magnet_links=magnet_links,
            )
            skipped_history += outcome.skipped_history
            no_new_torrents += outcome.no_new_torrents

            if outcome.visited_href:
                visited_hrefs.add(outcome.visited_href)
            if outcome.actor_update:
                actor_updates.append(outcome.actor_update)
            if outcome.row is not None:
                phase_rows.append(outcome.row)

            # P1-B: success path — mark the claim completed so peer
            # runners observe ``already_completed=True`` on subsequent
            # claim attempts in the same shard.  Only fires if we
            # actually leased this href (i.e. ``claim`` returned
            # ``acquired=True``); fail-open candidates skip this.
            # Idempotent on the Worker side.
            #
            # Bug fix: when ``complete`` fails (timeout / Unavailable /
            # unexpected error) or the Worker returns ``completed=False``
            # (stale-holder), the lease is still attributed to this
            # runner on the Worker side.  Discarding the href from
            # ``held_claims`` without releasing would skip the
            # ``finally:`` cleanup below and force peers to wait for
            # the 30-minute TTL.  Fall back to an explicit ``release``
            # so the slot frees up promptly.
            if href in held_claims:
                if not _complete_movie_claim(href, shard_date):
                    _release_movie_claim(href, shard_date)
                held_claims.discard(href)

            current_runtime_state = backend.runtime_state()
            result.acknowledge(
                outcome.status,
                runtime_state_changed=(
                    current_runtime_state != previous_runtime_state
                ),
            )
            previous_runtime_state = current_runtime_state
    finally:
        # P1-B: any claims still held at this point belong to tasks that
        # never returned a result (e.g. early shutdown, unhandled
        # exception, or backend-level failure).  Release them so peer
        # runners do not have to wait for the TTL — the Worker's GC
        # alarm will eventually mop up either way, but a prompt release
        # tightens the recovery window from minutes to milliseconds.
        for stuck_href in list(held_claims):
            _release_movie_claim(stuck_href, shard_date)
        held_claims.clear()
        backend.shutdown()

    finalize_detail_phase(
        use_history_for_saving=use_history_for_saving,
        dry_run=dry_run,
        history_file=history_file,
        visited_hrefs=visited_hrefs,
        actor_updates=actor_updates,
    )

    logger.info(
        f"Phase {phase} completed: {total_entries} movies discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    runtime_state = backend.runtime_state()
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'failed_movies': failed_movies,
        'no_new_torrents': no_new_torrents,
        'use_proxy': runtime_state.use_proxy,
        'use_cf_bypass': runtime_state.use_cf_bypass,
    }


def prepare_detail_entries(
    entries: List[dict],
    *,
    history_data: dict,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    enable_redownload: bool = False,
    include_recent_release_filters: bool = False,
    log_duplicate_skips: bool = False,
) -> tuple[List[DetailEntryCandidate], int]:
    """Filter raw entries into detail-page candidates for fetching."""

    total_entries = len(entries)
    prepared: List[DetailEntryCandidate] = []
    local_parsed_links: set[str] = set()
    skipped_history = 0

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        page_num = entry['page']

        if href in state.parsed_links or href in local_parsed_links:
            if log_duplicate_skips:
                logger.info(
                    f"[{i}/{total_entries}] [Page {page_num}] "
                    "Skipping duplicate entry in current run"
                )
            continue

        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            skip_complete = True
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (
                    should_skip_recent_today_release(href, history_data, is_today)
                    or should_skip_recent_yesterday_release(
                        href,
                        history_data,
                        is_yesterday,
                    )
                ):
                    skip_complete = False
                    logger.debug(
                        f"[{i}/{total_entries}] [Page {page_num}] "
                        f"{entry['video_code']} has complete subtitles but "
                        "re-download check enabled"
                    )
            if skip_complete:
                logger.info(
                    f"[{i}/{total_entries}] [Page {page_num}] "
                    f"Skipping {entry['video_code']} - already has subtitle "
                    "and hacked_subtitle in history"
                )
                skipped_history += 1
                continue

        if (
            rclone_filter
            and rclone_inventory
            and should_skip_from_rclone(
                entry.get('video_code', ''),
                rclone_inventory,
                enable_dedup,
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - already exists in "
                "rclone inventory with 中字"
            )
            skipped_history += 1
            continue

        if (
            include_recent_release_filters
            and not is_adhoc_mode
            and should_skip_recent_yesterday_release(
                href,
                history_data,
                entry.get('is_yesterday_release', False),
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - yesterday release, "
                "recently updated in history"
            )
            skipped_history += 1
            continue

        if (
            include_recent_release_filters
            and not is_adhoc_mode
            and should_skip_recent_today_release(
                href,
                history_data,
                entry.get('is_today_release', False),
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - today release, "
                "already visited today"
            )
            skipped_history += 1
            continue

        prepared.append(
            DetailEntryCandidate(
                entry=entry,
                href=href,
                page_num=page_num,
                entry_index=f"{i}/{total_entries}",
            )
        )

    state.parsed_links.update(local_parsed_links)
    return prepared, skipped_history


def persist_parsed_detail_result(
    *,
    entry: dict,
    phase: int,
    entry_index: str = '',
    worker_name: str = '',
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
    actor_info: str = '',
    actor_gender: str = '',
    actor_link: str = '',
    supporting_actors: str = '',
    magnet_links: Optional[dict] = None,
) -> DetailPersistOutcome:
    """Build ingestion plan, write outputs, and return outcome metadata."""

    href = entry['href']
    video_code = entry['video_code']
    page_num = entry['page']
    actor_info = actor_info or ''
    actor_gender = actor_gender or ''
    actor_link = actor_link or ''
    supporting_actors = supporting_actors or ''
    magnet_links = magnet_links or {}

    outcome = DetailPersistOutcome(
        status='reported',
        visited_href=href,
        actor_update=(
            href,
            actor_info,
            actor_gender,
            actor_link,
            supporting_actors,
        ),
    )

    parsed_movie = ParsedMovie(
        href=href,
        video_code=video_code,
        page_num=page_num,
        actor_name=actor_info,
        actor_gender=actor_gender,
        actor_link=actor_link,
        supporting_actors=supporting_actors,
        magnet_links=magnet_links,
        entry=entry,
    )

    rclone_entries = []
    if rclone_inventory and video_code:
        rclone_entries = rclone_inventory.get(video_code.upper(), [])

    plan = build_spider_ingestion_plan(
        parsed_movie,
        history_data=history_data,
        phase=phase,
        rclone_entries=rclone_entries,
        enable_dedup=enable_dedup,
        enable_redownload=enable_redownload and not is_adhoc_mode,
        redownload_threshold=redownload_threshold,
    )

    if plan.should_skip:
        if entry_index:
            logger.debug(
                f"[{entry_index}] [Page {page_num}] "
                f"Skipping based on ingestion plan: {plan.skip_reason}"
            )
        outcome.status = 'skipped'
        outcome.skipped_history = 1
        return outcome

    worker_tag = f"[{worker_name}] " if worker_name else ""
    for rec in plan.dedup_records:
        if not dry_run and dedup_csv_path:
            append_dedup_record(dedup_csv_path, rec)
        if entry_index:
            logger.info(
                f"[{entry_index}] {worker_tag}DEDUP: {rec.video_code} - "
                f"{rec.deletion_reason}"
            )

    row = plan.report_row
    if row is None:
        outcome.status = 'no_row'
        outcome.no_new_torrents = 1
        return outcome

    if plan.should_include_in_report:
        write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
        outcome.row = row
        if (
            use_history_for_saving
            and not dry_run
            and plan.has_new_torrents
            and plan.new_magnet_links
        ):
            save_parsed_movie_to_history(
                history_file,
                href,
                phase,
                video_code,
                plan.new_magnet_links,
                size_links=plan.new_sizes,
                file_count_links=plan.new_file_counts,
                resolution_links=plan.new_resolutions,
                actor_name=actor_info,
                actor_gender=actor_gender,
                actor_link=actor_link,
                supporting_actors=supporting_actors,
            )
        return outcome

    outcome.status = 'not_included'
    outcome.no_new_torrents = 1
    return outcome


def finalize_detail_phase(
    *,
    use_history_for_saving: bool,
    dry_run: bool,
    history_file: str,
    visited_hrefs: set,
    actor_updates: list,
) -> None:
    """Flush shared per-phase side effects after detail processing completes."""

    if use_history_for_saving and not dry_run and visited_hrefs:
        if use_sqlite() and actor_updates:
            db_batch_update_movie_actors(actor_updates)
        batch_update_last_visited(history_file, visited_hrefs)
