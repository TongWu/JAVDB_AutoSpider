#!/usr/bin/env python3
"""Index freshness probe — design-support tool for dynamic daily pagination.

Fetches the daily index pages (``BASE_URL?page=N``) and reports, per page:

* how many entries carry the "today / yesterday new torrent" tags,
* how many entries the production phase-1 / phase-2 gates would select,
* the release dates the page spans (to test whether the listing is ordered by
  release date, which decides whether freshness is contiguous across pages).

It then simulates the proposed "stop after K consecutive pages with no fresh
entries" rule and reports what that rule would have missed on this run.

Read-only: no detail-page fetches, no database writes, no session lifecycle.

Usage::

    python3 -m apps.cli.ops.index_probe --pages 30
    python3 -m apps.cli.ops.index_probe --pages 30 --json logs/index_probe.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# Importing this module must stay side-effect free: a module-level chdir would
# move the caller's working directory (tests included) and silently retarget
# relative --json / --save-html paths at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.logging import setup_logging, get_logger  # noqa: E402

logger = get_logger("index_probe")

from javdb.parsing import parse_index_page  # noqa: E402
# The probe deliberately reuses the production tag predicates so its counts
# cannot drift from what the spider actually selects.
from javdb.pipeline.index_code_blacklist import (  # noqa: E402
    filter_blacklisted_code_keywords,
    load_daily_code_keyword_blacklist,
)
from javdb.pipeline.index_family_blacklist import (  # noqa: E402
    filter_blacklisted_families,
    load_daily_family_blacklist,
)
from javdb.pipeline.index_selection import (  # noqa: E402
    IGNORE_RELEASE_DATE_FILTER,
    _is_today_release,
    _is_yesterday_release,
    select_index_entries,
)
from javdb.spider.fetch.fallback import get_page_url, validate_index_html  # noqa: E402
from javdb.spider.fetch.index import build_page_scan_policy  # noqa: E402
from javdb.spider.fetch.page_scan import PageScanPolicy, effective_stop_after  # noqa: E402
from javdb.spider.runtime.config import (  # noqa: E402
    BASE_URL,
    PAGE_END,
    PAGE_SCAN_MAX,
    PAGE_SCAN_STOP_AFTER,
    PAGE_START,
)
from javdb.spider.runtime.state import should_use_proxy_for_module  # noqa: E402
from javdb.spider.spider_gateway import create_gateway  # noqa: E402


@dataclass
class PageProbe:
    """One index page's freshness measurements."""

    page: int
    url: str
    status: str                       # 'ok' | 'empty' | 'invalid' | 'fetch_failed'
    entries: int = 0
    today: int = 0
    yesterday: int = 0
    fresh: int = 0                    # today + yesterday
    fresh_positions: list[int] = field(default_factory=list)
    fresh_with_subtitle: int = 0      # fresh AND carries a CN-subtitle magnet tag
    p1_selected: int = 0              # exactly what the spider would take for phase 1
    p2_selected: int = 0              # exactly what the spider would take for phase 2
    release_dates: list[str] = field(default_factory=list)
    page_title: str = ''
    html_len: int = 0
    # Literal substring counts in the served HTML. Separates "the parser missed
    # the badges" from "the badges are not in the response at all".
    html_markers: dict[str, int] = field(default_factory=dict)
    # Every distinct badge text seen on the page, with counts. Tells apart "the
    # site served a listing with no fresh entries" from "the tags we key on are
    # not in this markup at all" — the two look identical in the fresh counts.
    tag_histogram: dict[str, int] = field(default_factory=dict)
    sample_entries: list[dict] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        """True when the page loaded and yielded a movie list."""
        return self.status == 'ok'


_SUBTITLE_TAGS = frozenset(['含中字磁鏈', '含中字磁链', 'CnSub DL'])

_HTML_MARKERS = (
    '今日新種', '今日新种', '昨日新種', '昨日新种', 'Today', 'Yesterday',
    '含磁鏈', '含中字磁鏈', 'tags has-addons', 'movie-list',
    # Login state: the badges may only render for a signed-in session.
    '/users/logout', '/login',
)


def probe_page(gateway, page_num: int, save_html_to: Optional[Path] = None) -> PageProbe:
    """Fetch, validate and measure a single index page."""
    url = get_page_url(page_num, custom_url=None)
    html = gateway.fetch_html(url)
    if html and save_html_to is not None:
        save_html_to.parent.mkdir(parents=True, exist_ok=True)
        save_html_to.write_text(html, encoding='utf-8')
        logger.info('[Page %d] saved raw HTML to %s', page_num, save_html_to)
    if not html:
        logger.warning("[Page %d] fetch failed", page_num)
        return PageProbe(page=page_num, url=url, status='fetch_failed')

    result_html, has_movie_list, is_valid_empty = validate_index_html(
        html, page_num=page_num, context_msg='index probe',
    )
    if result_html is None:
        logger.warning("[Page %d] validation failed (login wall / CF challenge?)", page_num)
        return PageProbe(page=page_num, url=url, status='invalid')
    if is_valid_empty or not has_movie_list:
        logger.info("[Page %d] valid but empty (end of pagination?)", page_num)
        return PageProbe(page=page_num, url=url, status='empty')

    page_result = parse_index_page(html, page_num)
    probe = PageProbe(page=page_num, url=url, status='ok')
    probe.page_title = getattr(page_result, 'page_title', '') or ''
    probe.html_len = len(html)
    probe.html_markers = {marker: html.count(marker) for marker in _HTML_MARKERS}

    for position, entry in enumerate(page_result.movies):
        probe.entries += 1
        probe.release_dates.append(entry.release_date or '')
        for tag in entry.tags:
            probe.tag_histogram[tag] = probe.tag_histogram.get(tag, 0) + 1
        if position < 3:
            probe.sample_entries.append({
                'video_code': entry.video_code,
                'tags': list(entry.tags),
                'release_date': entry.release_date,
                'rate': entry.rate,
                'comment_count': entry.comment_count,
            })
        is_today = _is_today_release(entry.tags)
        is_yesterday = _is_yesterday_release(entry.tags)
        if is_today:
            probe.today += 1
        if is_yesterday:
            probe.yesterday += 1
        if is_today or is_yesterday:
            probe.fresh += 1
            probe.fresh_positions.append(position)
            if _SUBTITLE_TAGS.intersection(entry.tags):
                probe.fresh_with_subtitle += 1

    # Production filters the daily blacklists out before selecting, so counting
    # selections on the raw page would credit entries that never get ingested —
    # and inflate `selected_missed` in the stop-rule simulation with them. The
    # freshness counting above deliberately runs first: per ADR-057 D2 a
    # blacklisted entry still proves the page sits inside the fresh block.
    family_blacklist = load_daily_family_blacklist(None)
    if family_blacklist:
        page_result.movies = filter_blacklisted_families(
            page_result.movies, family_blacklist, {},
        )
    code_blacklist = load_daily_code_keyword_blacklist(None)
    if code_blacklist:
        page_result.movies = filter_blacklisted_code_keywords(
            page_result.movies, code_blacklist, {},
        )

    probe.p1_selected = len(select_index_entries(page_result, page_num=page_num, phase=1))
    probe.p2_selected = len(select_index_entries(page_result, page_num=page_num, phase=2))

    logger.info(
        "[Page %2d] entries=%3d fresh=%3d (today=%d, yesterday=%d) "
        "p1=%2d p2=%2d dates=%s..%s",
        page_num, probe.entries, probe.fresh, probe.today, probe.yesterday,
        probe.p1_selected, probe.p2_selected,
        probe.release_dates[0] if probe.release_dates else '-',
        probe.release_dates[-1] if probe.release_dates else '-',
    )
    logger.info("[Page %2d] title=%r tags=%s", page_num, probe.page_title, probe.tag_histogram)
    logger.info(
        "[Page %2d] html_len=%d markers=%s",
        page_num, probe.html_len,
        {k: v for k, v in probe.html_markers.items() if v},
    )
    return probe


def _date_sort_key(value: str):
    """Comparable ``(year, month, day)`` for a rendered release date, or ``None``.

    JavDB renders dates per locale — ``2026-08-04`` in Chinese, ``08/04/2026``
    in English — so comparing the raw strings orders by leading digits and calls
    ``12/28/2012`` newer than ``08/08/2026``. Every date comparison in this
    module goes through this key.
    """
    parts = value.split('/')
    if len(parts) == 3:
        month, day, year = parts
    else:
        parts = value.split('-')
        if len(parts) != 3:
            return None
        year, month, day = parts
    try:
        # Ints, not strings: JavDB does not always zero-pad, and '8' sorts
        # above '12' lexicographically.
        return (int(year), int(month), int(day))
    except ValueError:
        return None


def _newest_date(dates: list[str]) -> str:
    """Newest date in *dates*, returned verbatim. Unparseable values are skipped."""
    keyed = [(_date_sort_key(d), d) for d in dates]
    keyed = [(k, d) for k, d in keyed if k is not None]
    return max(keyed)[1] if keyed else ''


def _daily_scan_would_extend() -> bool:
    """Whether the live daily scan actually runs the extension.

    Built through the production predicate rather than re-reading the flags, so
    the probe cannot drift from what `build_page_scan_policy()` decides. The
    probe always *simulates* the rule — that is what calibration means — but the
    report has to say whether the configuration in force would use it.
    """
    return build_page_scan_policy(
        end_page=PAGE_END, parse_all=False, custom_url=None,
        ignore_release_date=False,
    ).enabled


def simulate_stop(
    probes: list[PageProbe], k: int,
    floor_page: int = PAGE_END, max_page: int = PAGE_SCAN_MAX,
) -> tuple:
    """Where the production stop rule would have ended this scan, and why.

    Runs the real :class:`PageScanPolicy` over the probe's observations rather
    than reimplementing it, so the simulation cannot drift from — or flatter —
    the rule it exists to test. In particular the policy also stops on *k*
    consecutive unreadable pages, which a hand-rolled fresh-free counter would
    silently scan straight through.

    *floor_page* and *max_page* default to the configured ``PAGE_END`` and
    ``PAGE_SCAN_MAX`` because those are the bounds production scans within:
    pages up to the floor are fetched whatever their freshness, and the scan
    never goes past the cap. Simulating without them reports a quiet page 2 as
    a stop, and a still-fresh page 31 as reached, for a scan that would have
    done neither.

    Returns ``(stop_page, stop_reason)``; ``(None, None)`` if it never fires.
    """
    policy = PageScanPolicy(
        floor_page=floor_page,
        max_page=max_page,
        stop_after=k,
        enabled=True,
    )
    for probe in probes:
        policy.observe(
            probe.page,
            fresh=probe.fresh if probe.parsed else None,
            end_of_content=(probe.status == 'empty'),
        )
        if not policy.should_continue_after(probe.page):
            return probe.page, policy.stop_reason
    return None, None


def summarize(probes: list[PageProbe], pages_requested: Optional[int] = None) -> dict:
    """Aggregate the per-page probes into the numbers the design needs.

    *pages_requested* is what the caller asked for. It differs from the number
    attempted when the time budget cut the run short, and the probe still exits
    zero — so without it a truncated run looks like a complete, shallower one.
    """
    parsed = [p for p in probes if p.parsed]
    # Only a page we could not read hides anything. A structurally valid empty
    # page — the tail of pagination — is a *known* page with no entries, so it
    # must not be lumped in with the unknowns.
    unreadable = [p for p in probes if p.status in ('fetch_failed', 'invalid')]
    known = [p for p in probes if p.status in ('ok', 'empty')]
    fresh_pages = [p.page for p in parsed if p.fresh > 0]
    selected_pages = [p.page for p in parsed if (p.p1_selected + p.p2_selected) > 0]

    deepest_fresh = max(fresh_pages) if fresh_pages else None
    deepest_selected = max(selected_pages) if selected_pages else None

    # Holes: pages with zero fresh entries that still have fresh entries behind
    # them. A non-empty list disproves the "freshness is contiguous" assumption.
    holes = [
        p.page for p in known
        if p.fresh == 0 and deepest_fresh is not None and p.page < deepest_fresh
    ]
    # A page we could not read sits in the same range but tells us nothing: it
    # may or may not have been fresh. Reporting "contiguous" over such a gap
    # would let a transient proxy ban manufacture support for the stop rule, so
    # contiguity is left undetermined (None) instead.
    unknown_gaps = [
        p.page for p in unreadable
        if deepest_fresh is not None and p.page < deepest_fresh
    ]
    if deepest_fresh is None:
        # No fresh page anywhere — nothing to be contiguous about. Saying True
        # here would report "the assumption held" for a run that never tested it.
        contiguous = None
    elif holes:
        contiguous = False
    elif unknown_gaps:
        contiguous = None
    else:
        contiguous = True

    # Release-date ordering: does the listing march backwards in time? Compare
    # parsed keys — raw strings would order 12/28/2012 above 08/08/2026.
    dates_in_order: list[str] = []
    entries_with_date_slot = 0
    for probe in parsed:
        entries_with_date_slot += len(probe.release_dates)
        dates_in_order.extend(d for d in probe.release_dates if d)
    ordered_keys = [k for k in (_date_sort_key(d) for d in dates_in_order) if k]
    violation = any(
        ordered_keys[i] < ordered_keys[i + 1]
        for i in range(len(ordered_keys) - 1)
    )
    # A pair out of order is a counterexample wherever it sits, so `False` holds
    # no matter how much of the listing we sampled. "No counterexample found",
    # though, is only worth something over a complete sample: an entry whose
    # date did not parse, or a page we could not read, is exactly where a
    # violation would hide. Anything less than full coverage is undetermined —
    # all() over an empty range would otherwise report "date-descending" for a
    # scan that parsed no dates at all, and ADR-057 reads this field as the
    # reason release dates *cannot* be the stop signal.
    # An empty tail page carries no entries, so it hides no dates and does not
    # count against coverage; only a page we could not read does.
    sample_complete = (
        len(ordered_keys) >= 2
        and len(ordered_keys) == entries_with_date_slot
        and not unreadable
    )
    if violation:
        descending = False
    elif sample_complete:
        descending = True
    else:
        descending = None
    # The newest release date anywhere in the scan. When it lags the current
    # date, the listing simply has not received the day's releases yet — which
    # is a different reason for "no fresh entries" than a scan that ran too shallow.
    newest_release_date = _newest_date(dates_in_order)

    scan_truncated = pages_requested is not None and len(probes) < pages_requested

    simulations = {}
    # k=1..3 is the calibration sweep; the configured value is what production
    # actually runs, so it has to be in the set even when it sits outside it.
    # Floored the way the policy floors it, or a configured 0 would appear as a
    # `k=0` entry reporting what k=1 actually does.
    configured_k = effective_stop_after(PAGE_SCAN_STOP_AFTER)
    for k in sorted({1, 2, 3, configured_k}):
        stop_page, stop_reason = simulate_stop(
            probes, k, floor_page=PAGE_END, max_page=PAGE_SCAN_MAX,
        )
        scanned = [p for p in parsed if stop_page is None or p.page <= stop_page]
        missed = [p for p in parsed if stop_page is not None and p.page > stop_page]
        # Pages past the stop point that we never read. Their freshness is not
        # zero, it is unknown — without listing them, `fresh_missed: 0` reads as
        # "the rule lost nothing" for a tail the probe never saw.
        missed_unreadable = [
            p.page for p in unreadable
            if stop_page is not None and p.page > stop_page
        ]
        simulations[f'k={k}'] = {
            'stop_page': stop_page,
            'stop_reason': stop_reason,
            'fresh_scanned': sum(p.fresh for p in scanned),
            'fresh_missed': sum(p.fresh for p in missed),
            'selected_scanned': sum(p.p1_selected + p.p2_selected for p in scanned),
            'selected_missed': sum(p.p1_selected + p.p2_selected for p in missed),
            'pages_missed_with_fresh': [p.page for p in missed if p.fresh > 0],
            'pages_missed_unreadable': missed_unreadable,
            # The missed counts cover only the pages this run read. Three ways
            # they can fall short: a tail we could not read, a request the time
            # budget cut off, and — even on a scan that completed exactly what
            # was asked — a request too shallow for the rule to have decided
            # yet. `stop_page is None` means the probe data ran out before the
            # policy stopped, so production would have kept reading past it.
            'missed_counts_complete': (
                stop_page is not None
                and not missed_unreadable
                and not scan_truncated
            ),
        }
    # The bounds the simulation assumed, matching production. Pages up to the
    # floor are scanned whatever their freshness, so a `stop_page` is always at
    # or past it and a scan shallower than the floor can report no stop at all;
    # the cap is where a still-fresh scan gets cut off.
    simulations['floor_page'] = PAGE_END
    simulations['max_page'] = PAGE_SCAN_MAX
    # Which of the k=N entries above is the one production would produce — but
    # only if production runs the rule at all. The daily policy is built via the
    # same predicate, so a config with the extension switched off makes every
    # entry here a what-if rather than a description of the live scan.
    simulations['configured_stop_after'] = configured_k
    simulations['dynamic_scan_enabled'] = _daily_scan_would_extend()

    tag_histogram: dict[str, int] = {}
    for probe in parsed:
        for tag, count in probe.tag_histogram.items():
            tag_histogram[tag] = tag_histogram.get(tag, 0) + count

    return {
        'base_url': BASE_URL,
        'ignore_release_date_filter': bool(IGNORE_RELEASE_DATE_FILTER),
        'page_titles': sorted({p.page_title for p in parsed if p.page_title}),
        'tag_histogram': dict(sorted(tag_histogram.items(), key=lambda kv: -kv[1])),
        'pages_requested': len(probes) if pages_requested is None else pages_requested,
        'pages_attempted': len(probes),
        # True when the run stopped before working through the request — the
        # scan is shallower than asked for, so a clean report proves less.
        'scan_truncated': scan_truncated,
        'pages_parsed': len(parsed),
        'pages_failed': [p.page for p in unreadable],
        'pages_empty': [p.page for p in probes if p.status == 'empty'],
        'total_entries': sum(p.entries for p in parsed),
        'total_fresh': sum(p.fresh for p in parsed),
        'total_today': sum(p.today for p in parsed),
        'total_yesterday': sum(p.yesterday for p in parsed),
        'total_selected_p1': sum(p.p1_selected for p in parsed),
        'total_selected_p2': sum(p.p2_selected for p in parsed),
        'deepest_page_with_fresh': deepest_fresh,
        'deepest_page_with_selected': deepest_selected,
        'fresh_free_pages_before_deepest_fresh': holes,
        'unreadable_pages_before_deepest_fresh': unknown_gaps,
        # True / False / None — None means a fetch gap left it undetermined.
        'freshness_contiguous': contiguous,
        # True / False / None — None means the date sample was too incomplete to
        # rule out a violation. The two counts below say how incomplete.
        'release_dates_descending': descending,
        'release_dates_parsed': len(ordered_keys),
        'release_dates_seen': entries_with_date_slot,
        'newest_release_date': newest_release_date,
        'stop_rule_simulation': simulations,
    }


def render_table(probes: list[PageProbe]) -> str:
    lines = [
        'page | status       | entries | today | yest | fresh | p1 | p2 | first date | last date',
        '-----+--------------+---------+-------+------+-------+----+----+------------+----------',
    ]
    for p in probes:
        first = p.release_dates[0] if p.release_dates else '-'
        last = p.release_dates[-1] if p.release_dates else '-'
        lines.append(
            f'{p.page:4d} | {p.status:12s} | {p.entries:7d} | {p.today:5d} | '
            f'{p.yesterday:4d} | {p.fresh:5d} | {p.p1_selected:2d} | {p.p2_selected:2d} | '
            f'{first:10s} | {last:10s}'
        )
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Probe how deep today/yesterday new-torrent entries reach in the daily index.',
    )
    parser.add_argument('--pages', type=int, default=30, help='How many pages to scan (default: 30)')
    parser.add_argument(
        '--start-page', type=int, default=PAGE_START,
        help=f'First page to scan (default: PAGE_START = {PAGE_START}). Starting '
             'earlier than production would charge pages it never fetches '
             'against the consecutive fresh-free and unreadable budgets.',
    )
    parser.add_argument('--delay', type=float, default=2.0, help='Seconds between page fetches (default: 2.0)')
    parser.add_argument(
        '--max-seconds', type=float, default=0.0,
        help='Stop scanning after this many seconds and summarise what was collected '
             '(default: 0 = no budget). Guards against a CI job timeout discarding the run.',
    )
    parser.add_argument('--json', dest='json_path', default=None, help='Write the full result as JSON to this path')
    parser.add_argument(
        '--save-html', dest='save_html_dir', default=None,
        help='Directory to dump the raw HTML of the first two pages (markup diagnostics)',
    )
    parser.add_argument(
        '--no-proxy', action='store_true',
        help='Force a direct fetch, overriding PROXY_MODULES',
    )
    parser.add_argument(
        '--use-proxy', action='store_true',
        help='Force the proxy pool, overriding PROXY_MODULES',
    )
    parser.add_argument('--no-cf-bypass', action='store_true', help='Disable the CF bypass service')
    parser.add_argument('--use-cookie', action='store_true', help='Send the JavDB session cookie')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    # Pass the level in: setup_logging() also sets each handler's level from
    # LOG_LEVEL, so raising only the root logger would still let an INFO console
    # handler swallow the debug records --verbose is asking for.
    setup_logging(log_level='DEBUG' if args.verbose else None)

    if args.no_proxy and args.use_proxy:
        parser.error('--no-proxy and --use-proxy are mutually exclusive')
    # Default to whatever PROXY_MODULES says the spider's index fetch does —
    # the probe exists to reproduce that fetch, so forcing the pool on would
    # measure a path production may not take. The flags stay as overrides.
    proxy_override = True if args.use_proxy else (False if args.no_proxy else None)
    use_proxy = should_use_proxy_for_module('spider', proxy_override)
    use_cf_bypass = not args.no_cf_bypass
    gateway = create_gateway(
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
        use_cookie=args.use_cookie,
    )
    logger.info(
        'Probing %s pages %d-%d (use_proxy=%s [%s], use_cf_bypass=%s, use_cookie=%s)',
        BASE_URL, args.start_page, args.start_page + args.pages - 1,
        use_proxy,
        'forced' if proxy_override is not None else 'from PROXY_MODULES',
        use_cf_bypass, args.use_cookie,
    )

    def write_json(probes: list[PageProbe]) -> dict:
        payload = {
            'summary': summarize(probes, pages_requested=args.pages),
            'pages': [asdict(p) for p in probes],
        }
        if args.json_path:
            out = Path(args.json_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        return payload

    started = time.monotonic()
    probes: list[PageProbe] = []
    for page_num in range(args.start_page, args.start_page + args.pages):
        save_to = None
        if args.save_html_dir and page_num < args.start_page + 2:
            save_to = Path(args.save_html_dir) / f'index_page_{page_num}.html'
        probes.append(probe_page(gateway, page_num, save_html_to=save_to))
        # Flush after every page: index fetches are slow enough that a CI job
        # timeout would otherwise throw away the whole run.
        write_json(probes)
        if args.max_seconds and (time.monotonic() - started) >= args.max_seconds:
            logger.warning(
                'Time budget of %.0fs reached after page %d — stopping early',
                args.max_seconds, page_num,
            )
            break
        if page_num < args.start_page + args.pages - 1:
            time.sleep(args.delay)

    payload = write_json(probes)
    summary = payload['summary']

    print()
    print(render_table(probes))
    print()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.json_path:
        logger.info('Wrote %s', args.json_path)

    # Non-zero only when the probe could not do its job at all, so a CI run that
    # fetched nothing is not reported as a clean result.
    if summary['pages_parsed'] == 0:
        logger.error('Probe parsed no pages — proxy pool / CF bypass / site issue')
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
