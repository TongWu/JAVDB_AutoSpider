# BFR-013: MovieMetadata backfill misclassifies a login wall as `parse_failed`

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: Medium
**Affected**: `javdb/migrations/tools/backfill_movie_metadata.py` (`_process_href`, `run_backfill_metadata`)
**Related**: [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) (owns the `MovieMetadata` table), [IMP-ADR022-08](../_archive/ADR-022-User-Preference-Foundation/IMP-ADR022-08-metadata-backfill.md) (the backfill), [BFR-012](../BFR-012-RustMovieDetail-No-Dict-Metadata-Upsert/BFR-012-rustmoviedetail-no-dict-metadata-upsert.md) (the `__dict__` crash surfaced in the same run)

---

## Symptom

The Migration `--backfill-metadata` run reported a login-gated movie as a parse
failure:

```text
⚠ javdb.migrat  [meta-8/1000] https://javdb.com/v/a2nq3 — parse_failed: no metadata fields parsed
```

`/v/a2nq3` requires login to view. The operator could not tell from the log
whether the page was genuinely broken or simply needed a session cookie — both
collapse into `parse_failed`.

## Root Cause

The backfill fetches detail pages through `spider_state.get_page(...)` directly,
**bypassing the spider's `FetchEngine`**. That left two gaps the normal spider
path covers:

1. **Unauthenticated fetch.** `_process_href` used a bare `requests.Session()`
   and called `get_page(...)` *without* `use_cookie=True`, which defaults to
   `False`. The request handler only attaches the `_jdb_session` cookie when
   `use_cookie` is true (`javdb/infra/request.py` — `if use_cookie and
   self.config.javdb_session_cookie: headers['Cookie'] = ...`). So login-gated
   movies returned a login wall, with no metadata fields.

2. **No login-wall detection.** `_process_href` only checked
   `if not (video_code or title): parse_failed`. It had no `login_required`
   concept at all — any page without metadata fields became `parse_failed`
   (or `fetch_failed` if the body was empty). The sibling tool
   `align_inventory_with_moviehistory.py` *does* classify `login_required`,
   because it routes through `FetchEngine`, whose `ctx.fetch` raises
   `LoginRequired` on a login page (detected via `is_login_page`). The backfill,
   using the bare `get_page` path, never saw that signal.

**Why the design was wrong, not just what broke.** Two fetch paths exist for
detail pages: the login-aware `FetchEngine` (used by daily/ad-hoc ingestion) and
the bare `get_page` (used by one-shot tools). The backfill chose the bare path
for simplicity — reasonable — but silently inherited *none* of the login
handling, so a recoverable, well-understood condition (expired cookie / gated
content) was indistinguishable from a genuine parse failure.

### Does the same failure happen in daily / ad-hoc ingestion?

No — both route through `FetchEngine` and handle login explicitly:

- **Ad-hoc ingestion** fetches authenticated (`use_cookie = custom_url is not
  None` ⇒ `True` in `run_service.py`), so login-gated detail pages render and
  parse normally.
- **Daily ingestion** fetches *unauthenticated* (`use_cookie = False`, same as
  the old backfill), so it hits the same login walls — but `ctx.fetch` calls
  `is_login_page(html)` and raises `LoginRequired`, which the login coordinator
  handles by performing a real login and retrying with the cookie. So daily
  **detects and recovers** rather than mislabelling the page `parse_failed`.
  (Caveat: detection relies on `is_login_page` markers — `<title>` contains
  `登入`/`login`, or the copyright-restriction text — so a login wall that
  renders a normal `<title>` with hidden content would still parse empty.)

Only the backfill had neither authentication nor login detection.

## Fix

Option A — make the backfill login-aware (close both gaps), mirroring the
ad-hoc spider's authenticated fetch:

- **Authenticate**: pass `use_cookie=True` to `get_page` so the configured
  `JAVDB_SESSION_COOKIE` is attached and login-gated movies yield metadata. The
  cookie only attaches when configured, so an unconfigured/empty cookie degrades
  to an unauthenticated fetch (then handled by the next point).
- **Detect**: after a non-empty fetch, call `is_login_page(html)`; if true,
  return a distinct `login_required` result with a "refresh
  `JAVDB_SESSION_COOKIE`" message instead of `parse_failed`.
- **Report, don't fail**: `run_backfill_metadata` counts `login_required`
  separately (not a hard failure — the page is fine, the session isn't), logs a
  per-href warning, and emits a summary hint to run `python3 -m apps.cli.login`
  and re-run. Job exit code still keys off genuine `failed` only.

Tests (`tests/unit/test_backfill_movie_metadata_fetch.py`):

- `test_process_href_fetch_authenticates_with_session_cookie` — asserts the
  fetch requests `use_cookie=True`.
- `test_process_href_login_wall_is_login_required` — feeds a real login-titled
  page (`<title>登入 …</title>`) through the actual `is_login_page` detector,
  asserts `login_required`, and that parse/upsert are short-circuited.

## Side Effects

None negative. `login_required` is a new, non-fatal result status; existing
`ok`/`parse_failed`/`fetch_failed`/`write_failed` classifications are unchanged.
Authenticated fetches now capture metadata for login-gated movies that the
unauthenticated backfill could never retrieve.

## Follow-Up

- [ ] After landing, re-run `--backfill-metadata` with a fresh
      `JAVDB_SESSION_COOKIE` to backfill the login-gated movies that previously
      reported `parse_failed`.
- [ ] Other one-shot tools that fetch via bare `get_page` (rather than
      `FetchEngine`) share the same blind spot — audit if/when they need
      login-gated content. `is_login_page` is the reusable detector.
