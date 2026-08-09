# Dynamic Daily Index Pagination Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. Each task is test-first: write the failing test, watch it fail, then make it pass.

**Related:** [ADR-057](ADR-057-dynamic-daily-index-pagination.md) (the approved design), [ADR-044](../_archive/ADR-044-Index-Video-Code-Family-Blacklist/ADR-044-index-video-code-family-blacklist.md) (family blacklist ordering), [ADR-045](../_archive/ADR-045-FetchEngine-Public-API/ADR-045-fetch-engine-public-api.md) (parallel fetch backend)

**Goal:** Stop the daily index scan from truncating a heavy day at `PAGE_END`. Keep `PAGE_START..PAGE_END` as a floor, then extend page by page while pages still carry today/yesterday badges, stopping after K consecutive fresh-free pages or at a hard cap — with the effective last page and stop reason reported.

**Architecture:** A `PageScanPolicy` value object owns the whole rule. Both fetch paths feed it one observation per page (`page_num`, whether the page parsed, and its raw fresh count) and ask it whether to keep going. The sequential loop consults it where the `page_num >= end_page` break used to be; the parallel path consults it when advancing its sliding window. Freshness comes from a new `count_new_release_entries()` in `index_selection`, which reuses the same tag frozensets as selection so both badge locales stay covered.

**Tech Stack:** Python 3.11, `pytest`. Pure Python — no Rust and no DB schema change.

---

## Design Decisions Reference (from ADR-057)

- **D1** Freshness = raw today/yesterday badge count on the parsed page, not the phase-1/phase-2 selection count.
- **D2** Count *before* `filter_blacklisted_families`.
- **D3** `PAGE_START..PAGE_END` is a floor; the policy only decides whether to go past `PAGE_END`.
- **D4** Stop after K = 2 consecutive fresh-free pages (`PAGE_SCAN_STOP_AFTER`).
- **D5** A failed/invalid page is unknown: it neither advances nor resets the fresh-free run, but K consecutive unreadable pages end the extension (`unreadable`).
- **D6** Hard cap `PAGE_SCAN_MAX` (30); reaching it logs a warning.
- **D7** Daily mode only — off for `custom_url`, `--ignore-release-date`, `IGNORE_RELEASE_DATE_FILTER`, `parse_all`.
- **D8** One policy object shared by both paths; the parallel path caches its parsed page so the HTML is parsed once.
- **D9** Effective last page in the summary block and `SPIDER_STAT_PAGES`; stop reason alongside it in the summary block, in the result JSON's `stats.pages`, and on its own `SPIDER_STAT_PAGE_SCAN_STOP_REASON` line so the page field stays a bare range. Nothing new persisted. *(Amended during implementation: the plan originally folded the reason into `SPIDER_STAT_PAGES`, which would have broken that field for shell parents.)*

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `javdb/pipeline/index_selection.py` | Add `count_new_release_entries(page_result)` | Modify |
| `javdb/spider/fetch/page_scan.py` | `PageScanPolicy` + `PageScanOutcome` | **Create** |
| `javdb/spider/fetch/index.py` | Sequential loop consults the policy; return effective last page + stop reason | Modify |
| `javdb/spider/fetch/index_parallel.py` | Cache parsed page in `_index_parse_fn`; window advance + stop via the policy | Modify |
| `javdb/spider/runtime/config.py` | `PAGE_SCAN_DYNAMIC` / `PAGE_SCAN_MAX` / `PAGE_SCAN_STOP_AFTER` | Modify |
| `javdb/infra/config_generator.py` | Map the three `VAR_*` env vars | Modify |
| `config.py.example` | Document the three options | Modify |
| `javdb/spider/runtime/report.py` | Report the effective page range + stop reason | Modify |
| `javdb/spider/app/run_service.py` | Thread the scan result into the summary | Modify |
| `.github/workflows/DailyIngestion.yml` | Wire the three `VAR_*` | Modify |
| `tests/unit/test_page_scan_policy.py` | Policy state machine | **Create** |
| `tests/unit/test_index_selection_freshness.py` | Freshness counter | **Create** |
| `tests/unit/test_index_dynamic_pagination.py` | Sequential + parallel end-to-end with a fake fetch | **Create** |
| `docs/handbook/{en,zh}/self-hoster/configuration.md` | Document the options | Modify |
| `docs/handbook/{en,zh}/self-hoster/github-actions-setup.md` | Document the repository variables | Modify |

---

### Task 1: Freshness counter in `index_selection`

**Files:** Modify `javdb/pipeline/index_selection.py` (after `_has_release_date`, line ~35)

- [ ] **Step 1 — failing test.** Create `tests/unit/test_index_selection_freshness.py` asserting that `count_new_release_entries` counts entries carrying either date badge, in both locales, ignores entries with only magnet badges, counts an entry with no `video_code` (selection skips those, the freshness signal must not), and returns 0 for a page with `has_movie_list=False`.
- [ ] **Step 2 — run it, expect `ImportError`.**
- [ ] **Step 3 — implement.** `def count_new_release_entries(page_result) -> int:` returning `sum(1 for e in page_result.movies if _has_release_date(e.tags))`, guarded by `if page_result is None or not page_result.has_movie_list: return 0`.
- [ ] **Step 4 — green.** `pytest tests/unit/test_index_selection_freshness.py -v`

**Verify:** the function is independent of `IGNORE_RELEASE_DATE_FILTER` and of the phase gates — it is a site-signal counter, not a selection helper.

### Task 2: `PageScanPolicy`

**Files:** Create `javdb/spider/fetch/page_scan.py`, `tests/unit/test_page_scan_policy.py`

- [ ] **Step 1 — failing tests** covering:
  - disabled policy → `should_continue_after(page)` is False for `page >= floor_page` (exact current behaviour), reason `floor`;
  - fresh page at the floor → continue;
  - K consecutive fresh-free pages past the floor → stop, reason `exhausted`;
  - a fresh page between two fresh-free ones resets the run (K=2 needs two *consecutive*);
  - an unparsed page (`fresh=None`) neither advances nor resets the fresh-free run, and K consecutive unreadable pages stop the scan with reason `unreadable` (D5);
  - reaching `max_page` → stop, reason `cap`, and `hit_cap` is True;
  - a page below the floor never stops the scan, even with zero fresh entries (D3);
  - `end_of_content` short-circuits regardless of freshness.
- [ ] **Step 2 — run, expect `ModuleNotFoundError`.**
- [ ] **Step 3 — implement.**

```python
@dataclass
class PageScanPolicy:
    floor_page: int          # last page of the configured range (PAGE_END)
    max_page: int            # hard cap (PAGE_SCAN_MAX)
    stop_after: int          # K (PAGE_SCAN_STOP_AFTER)
    enabled: bool            # D7 gate
```

State: `_fresh_free_run: int`, `_stop_reason: str | None`, `_last_page: int`.
API: `observe(page_num, *, fresh: int | None, end_of_content: bool = False)`, `should_continue_after(page_num) -> bool`, `stop_reason`, `hit_cap`.

- [ ] **Step 4 — green + full unit run** for the new file.

**Verify:** the policy has no I/O, no logging of its own, and no knowledge of phases — the loops own those.

### Task 3: Configuration surface

**Files:** `javdb/spider/runtime/config.py` (near `PAGE_START` / `PAGE_END`, line ~26), `javdb/infra/config_generator.py` (~line 367), `config.py.example` (~line 347)

- [ ] **Step 1 — failing test** in `tests/unit/test_page_scan_policy.py`: importing the three names from `javdb.spider.runtime.config` yields the documented defaults (`True`, `30`, `2`).
- [ ] **Step 2 — implement** `PAGE_SCAN_DYNAMIC = cfg('PAGE_SCAN_DYNAMIC', True)`, `PAGE_SCAN_MAX = cfg('PAGE_SCAN_MAX', 30)`, `PAGE_SCAN_STOP_AFTER = cfg('PAGE_SCAN_STOP_AFTER', 2)`.
- [ ] **Step 3 — generator rows** in the `SPIDER CONFIGURATION` section, using `get_env_bool` / `get_env_int`.
- [ ] **Step 4 — `config.py.example`** entries with comments explaining floor / cap / K.

**Verify:** `VAR_PAGE_SCAN_DYNAMIC=false python3 -m apps.cli.ops.config_generator --github-actions --dry-run | grep PAGE_SCAN` renders all three.

### Task 4: Sequential path

**Files:** `javdb/spider/fetch/index.py` (`_fetch_all_index_pages_sequential`, the `page_num >= end_page` break at line ~242)

- [ ] **Step 1 — failing test** in `tests/unit/test_index_dynamic_pagination.py`: a fake `fetch_index_page_with_fallback` serving pages whose fresh counts are `[40, 40, 0, 0]` with `end_page=2` must fetch pages 1–4 and stop (extension + K=2), while the same fake with `PAGE_SCAN_DYNAMIC=False` fetches exactly pages 1–2.
- [ ] **Step 2 — run, expect the dynamic case to stop at page 2.**
- [ ] **Step 3 — implement.** Build the policy once before the loop (enabled iff `not parse_all and custom_url is None and not ignore_release_date and not IGNORE_RELEASE_DATE_FILTER`); call `policy.observe(...)` right after `parse_index_page` and **before** `filter_blacklisted_families` (D2); replace the hard break with `if not parse_all and not policy.should_continue_after(page_num): break`. Feed `fresh=None` on the fetch-failure / no-movie-list branches (lines ~164–180) so D5 holds, and `end_of_content=True` on the valid-empty branch (~line 160).
- [ ] **Step 4 — green.**

**Verify:** with the policy disabled the loop is byte-for-byte equivalent in behaviour to today (`page_num >= end_page` → break).

### Task 5: Parallel path

**Files:** `javdb/spider/fetch/index_parallel.py`

- [ ] **Step 1 — failing test** in `tests/unit/test_index_dynamic_pagination.py`: drive `fetch_all_index_pages_parallel` with a fake backend whose pages 1–6 are fresh and 7–8 are not, `end_page=3`; assert pages 1–8 are submitted and page 9 is not, and that each page's HTML is parsed exactly once (count `parse_index_page` calls).
- [ ] **Step 2 — run, expect submissions to stop at page 3.**
- [ ] **Step 3 — implement:**
  - `_index_parse_fn` (line ~53) additionally returns `'page_result': parse_index_page(html, page_num)` and `'fresh': count_new_release_entries(page_result)` so the worker parses once (D8);
  - the fixed-range submission branch (~line 168) becomes a floor: submit `start_page..end_page` up front, then extend inside the collection loop;
  - in the collection loop (~line 244), feed every result to the policy and submit the next page while `policy.should_continue_after(highest_contiguous_page)`; keep the existing `parse_all` sliding-window behaviour untouched;
  - the post-loop selection (~line 308) reuses `data['page_result']` instead of re-parsing.
- [ ] **Step 4 — green**, plus `pytest tests/unit/test_index_parallel.py -v` to prove the existing window/stop tests still pass.

**Verify:** `_check_stop_condition`'s existing empty/failed semantics are unchanged; freshness is an *additional* stop reason, never a replacement.

### Task 6: Reporting

**Files:** `javdb/spider/fetch/index.py` (both return dicts), `javdb/spider/runtime/report.py` (line ~57 signature, ~77 `pages` pair, ~104 `SPIDER_STAT_PAGES`), `javdb/spider/app/run_service.py` (~line 805 call site)

- [ ] **Step 1 — failing test**: `generate_summary_report(..., effective_end_page=14, page_scan_stop_reason='cap')` prints `SPIDER_STAT_PAGES=1-14` plus `SPIDER_STAT_PAGE_SCAN_STOP_REASON=cap`, and the summary pair shows the cap.
- [ ] **Step 2 — implement.** Both index paths return `effective_end_page` and `page_scan_stop_reason`; `run_service` forwards them; `report.py` prefers the effective value over the configured one and appends the reason. Log a `WARNING` when the reason is `cap`.
- [ ] **Step 3 — green.**

**Verify:** `ReportSessions.EndPage` receives `effective_end_page or last_valid_page`, so D1 — the canonical source — records how far the scan actually reached rather than the last page that happened to have content. No schema work either way. *(Amended during implementation: the plan originally kept `last_valid_page`, which left the D1 session range disagreeing with the run result on any extended or truncated scan.)*

### Task 7: Workflow wiring

**Files:** `.github/workflows/DailyIngestion.yml` (next to `VAR_PAGE_START` / `VAR_PAGE_END`, line ~255)

- [ ] Add `VAR_PAGE_SCAN_DYNAMIC`, `VAR_PAGE_SCAN_MAX`, `VAR_PAGE_SCAN_STOP_AFTER` with the ADR defaults as `||` fallbacks.
- [ ] Check the other workflows that render `config.py` for a daily-mode spider run and wire the same three where they apply.
- [ ] **Verify:** `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/DailyIngestion.yml'))"`.

### Task 8: Documentation

- [ ] `docs/handbook/en/self-hoster/configuration.md` — the three options next to `PAGE_START` / `PAGE_END`, explaining floor / extension / cap and when the extension is inert (D7).
- [ ] `docs/handbook/en/self-hoster/github-actions-setup.md` — the three repository variables.
- [ ] Mirror both into `docs/handbook/zh/...` in the same commit (code blocks, env names and defaults verbatim).
- [ ] **Verify:** `grep -rn "PAGE_SCAN" docs/handbook/` lists en and zh symmetrically.

### Task 9: Full verification

- [ ] `python3 -m pytest tests/unit -q` (or the impact-selected subset) — green.
- [ ] `python3 -m pytest tests/smoke -q` — green.
- [ ] Dry-run the daily spider path locally if a config is available: `python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 2` and confirm the log reports an effective end page and a stop reason.
- [ ] Re-read the diff for stray edits; confirm no production behaviour changes when `PAGE_SCAN_DYNAMIC=False`.

---

## Rollback

Set the repository variable `PAGE_SCAN_DYNAMIC=false`. The policy short-circuits at the floor and both loops behave exactly as before; no data or schema is affected.
