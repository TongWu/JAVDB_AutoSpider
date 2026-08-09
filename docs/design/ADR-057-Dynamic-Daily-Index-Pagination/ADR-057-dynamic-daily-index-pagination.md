# ADR-057: Dynamic Daily Index Pagination

**Status:** Accepted
**Date:** 2026-08-08
**Author:** Ted
**Related Implementation Plans:** [IMP-ADR057-01](IMP-ADR057-01-dynamic-daily-index-pagination.md) (Phase 1 — freshness-driven page scan across both fetch paths)
**D1 Write Class:** n/a <!-- no new writes: the effective last page already lands in ReportSessions.EndPage -->

## Context

Daily ingestion scans a fixed index page range, `PAGE_START..PAGE_END`. At the
time of writing the two defaults disagreed — GitHub Actions ran `1..10` while
`config.py.example` shipped `1..20` — and this ADR's implementation settled both
on `1..10`, since under the decision below the value becomes the scan *floor*
and a split default would give local runs a deeper base scan than production.
The range is a
guess: the run stops at page 10 whether the day's new torrents ended on page 3
or still filled page 10. On a heavy day — a studio backlog, a re-seed wave — the
tail of the day's new torrents falls off the end of the scan and is silently
lost. Nothing in the run reports that it happened.

The obvious fix is "keep going until a page has no today/yesterday entries", but
that rule is only safe if freshness is actually contiguous from page 1. That was
unverified, and a naive version of the rule can lose *more* than the fixed range
does: stopping at the first page that yields no *selected* entries would have
stopped on page 1 of the measured sample, where 40 fresh entries produced 0
phase-1 and 4 phase-2 selections.

To settle it, [`javdb/ops/index_probe.py`](../../../javdb/ops/index_probe.py)
was built and run against the live site through the production proxy pool
(20 pages, 800 entries, 2026-08-08 20:36 SGT — minutes after that day's daily
run), measuring per page: total entries, raw today/yesterday badge counts, what
the production phase-1/phase-2 gates would select, and the release-date span.

| page | entries | today | yesterday | fresh | p1 | p2 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 40 | 40 | 0 | 40 | 0 | 4 |
| 2 | 40 | 40 | 0 | 40 | 0 | 5 |
| 3 | 40 | 40 | 0 | 40 | 0 | 19 |
| 4 | 40 | 40 | 0 | 40 | 0 | 15 |
| 5 | 40 | 33 | 7 | 40 | 0 | 15 |
| 6 | 40 | 0 | 40 | 40 | 20 | 11 |
| 7 | 40 | 0 | 17 | 17 | 0 | 3 |
| 8–20 | 40 | 0 | 0 | 0 | 0 | 0 |

What the measurement establishes:

- **Freshness is contiguous and front-loaded.** `deepest_page_with_fresh = 7`,
  no fresh-free page before it, and the today → yesterday transition is ordered
  (pages 1–4 all today, page 5 mixed, page 6 all yesterday, page 7 partial).
  The listing behaves as if ordered by torrent-publish time.
- **The listing is not ordered by release date** (`release_dates_descending:
  false`; one page spanned 2012 to 2026). Release date cannot be the stop
  signal; the badges must be.
- **A stop rule would have lost nothing.** Simulated K=1/2/3 all stop with
  `fresh_missed: 0` and `selected_missed: 0`. The stop pages recorded at the
  time were 8/9/10, measured before the simulation took the `PAGE_END` floor
  into account; re-running it now reports 10 for every K, since the floor is
  scanned whatever its freshness. The missed counts are what the decision
  rests on, and they are unchanged.
- **The fixed limit is not currently truncating, with three pages of margin.**
  The day's boundary was page 7 against `PAGE_END = 10`.
- **Selection counts are the wrong signal.** Page 1: 40 fresh, `p1 = 0`.
- **Badge locale varies per response.** Earlier probe runs saw `今日新種` /
  `含磁鏈`; this one saw `Today` / `DL`. Both sets are already covered by
  `index_selection`'s frozensets.

This is a single day's sample, which is why the decision below keeps the
configured range as a floor rather than replacing it.

## Decision

The daily index scan keeps `PAGE_START..PAGE_END` as a **floor**, then **extends
past it page by page while the pages still carry today/yesterday badges**,
stopping after K consecutive fresh-free pages or at a hard cap.

Behaviour is therefore never narrower than today's; the change only adds pages
on days when the fixed end would have truncated.

### Design Decisions

D1. **The freshness signal is a raw badge count, not a selection count** —
count entries whose tags intersect the today/yesterday sets on the parsed page,
reusing `index_selection`'s frozensets so both locales stay covered. Counting
selections instead would stop on page 1 (40 fresh, 0 phase-1 selected).

D2. **Count before the family blacklist filter** — a blacklisted entry still
proves the page sits inside the fresh block. Counting after
`filter_blacklisted_families` would stop the scan early on a page the blacklist
happened to empty.

D3. **The configured range is a floor, not a target** — pages
`PAGE_START..PAGE_END` are always scanned; the policy only decides whether to
continue past `PAGE_END`. This makes the change strictly non-regressive against
the current behaviour, which matters because the evidence is one day deep.

D4. **Stop after K = 2 consecutive fresh-free pages** (`PAGE_SCAN_STOP_AFTER`).
The sample says K = 1 would have sufficed; K = 2 costs one extra index fetch
(~10s) and absorbs a one-page hole should the ordering ever be less perfect than
the sample.

D5. **A page that failed to fetch or validate is unknown, not fresh-free** — it
neither advances nor resets the fresh-free run. Otherwise one transient proxy
ban or CF challenge mid-scan silently truncates the day. Unknown pages are
counted on their own budget, though: K consecutive unreadable pages end the
extension with reason `unreadable`, because a page we could not read is no
evidence that the fresh block continues either. (Found during implementation —
without the budget, a proxy outage marched the scan to the cap fetching
nothing.)

D6. **Hard cap `PAGE_SCAN_MAX` (default 30)** — reaching it logs a warning and
records the stop reason, because hitting the cap is exactly the truncation this
ADR set out to detect. It also bounds the blast radius if the badge markup ever
changes such that every page looks fresh. For that warning to mean anything, an
already-satisfied stop condition outranks the ceiling: a scan whose fresh-free
(or unreadable) budget ran out *on* the last allowed page ended on its own terms
and reports `exhausted` / `unreadable`, not `cap`.

D7. **Daily mode only** — the extension is disabled for ad-hoc URLs
(`custom_url`), for `--ignore-release-date`, for `IGNORE_RELEASE_DATE_FILTER`,
and for `--all` (`parse_all`, which already means "scan to the end"). None of
those have a freshness signal to steer on; they keep today's behaviour exactly.

D8. **One policy object, consulted by both fetch paths** — the sequential loop
and the parallel sliding window share a single `PageScanPolicy` rather than
duplicating the rule. The parallel path additionally caches each page's parsed
result at validation time so the policy and the post-loop selection do not parse
the same HTML twice.

D9. **The run reports where and why it stopped** — the effective last page and
a stop reason (`floor` / `exhausted` / `unreadable` / `cap` / `end-of-content` /
`proxies-exhausted`) go into the
summary block, the run-result JSON's `stats.pages`, and a dedicated
machine-readable `SPIDER_STAT_PAGE_SCAN_STOP_REASON` line, so a capped
(potentially truncated) run is visible instead of looking like a clean one. The
reason gets its own stdout line rather than being appended to
`SPIDER_STAT_PAGES` so that field stays a bare page range for shell parents.
Nothing new is persisted: `SpiderStats` has a fixed column set, and the
effective last page already lands in `ReportSessions.EndPage`, so recording the
reason is not worth a D1 migration.

## Consequences

### Positive

- A day whose new torrents run past `PAGE_END` is ingested in full instead of
  silently truncated.
- Truncation becomes observable: hitting the cap is logged and recorded.
- Quiet days get no slower — the floor is unchanged and the extension only
  triggers when page `PAGE_END` is still fresh.
- The stop rule is measured, not assumed, and the probe that measured it stays
  in the repo for re-measurement.

### Negative

- Heavy days cost extra index fetches (~10s each at current proxy health), plus
  the K = 2 confirmation pages.
- One more configuration surface (three variables) to keep in sync across
  `config.py.example`, the config generator, and the workflows.
- The rule inherits the badge contract: if JavDB renames the badges in a locale
  we do not cover, freshness reads as zero and the scan falls back to the floor
  — the same output as today's fixed scan, but with the extension silently
  inert. The cap says nothing here (a zero-freshness scan stops at the floor
  with `exhausted`, never reaching it), so the existing site-contract sentinel
  is the only thing standing between that drift and a quiet loss of coverage.
- Evidence is a single day. If a later probe finds holes in the fresh block,
  `PAGE_SCAN_STOP_AFTER` is the knob to raise.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR057-01](IMP-ADR057-01-dynamic-daily-index-pagination.md) | `PageScanPolicy` + freshness counter, both fetch paths, config + workflow wiring, stop-reason reporting, docs | Multi-day probe calibration of K and the cap; a sentinel alert on "fresh count zero N days running" |

## References

- [`javdb/ops/index_probe.py`](../../../javdb/ops/index_probe.py) — the probe that produced the table above
- [ADR-044](../_archive/ADR-044-Index-Video-Code-Family-Blacklist/ADR-044-index-video-code-family-blacklist.md) — the family blacklist D2 orders against
- [ADR-045](../_archive/ADR-045-FetchEngine-Public-API/ADR-045-fetch-engine-public-api.md) — the parallel fetch backend the sliding window runs on
- [BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.md) — why D5 refuses to read a failed fetch as "no fresh entries"

## Status Log

- 2026-08-08: Proposed
- 2026-08-08: Accepted after the live probe confirmed contiguous, front-loaded freshness
