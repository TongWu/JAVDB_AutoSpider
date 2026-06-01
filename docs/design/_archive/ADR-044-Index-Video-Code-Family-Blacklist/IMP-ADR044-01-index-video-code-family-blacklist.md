# IMP-ADR044-01: Index Video Code Family Blacklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Related:** [ADR-044](ADR-044-index-video-code-family-blacklist.md) - this is **Phase 1**.

**Status:** Completed — implemented and verified on 2026-06-01. Recognition remains strictly additive, the blacklist is config-only, and filtering is an independent pre-selection step.

**Goal:** Teach the index parser to recognize `western_studio_date` video codes **additively** (without rejecting any token the parser accepts today), label every index-card `video_code` with a `video_code_family`, then keep the western family out of daily ingestion by default via a config-driven, daily-only pre-selection filter. Ad hoc ingestion is unchanged.

**Architecture:** The parser owns recognition. `classify_video_code_family()` is a read-only labeller (Python + Rust) that returns one of six family labels or `""`. `_is_plausible_video_code` stays exactly as permissive as today and is widened **only** to also accept dotted western tokens (via `WESTERN_STUDIO_DATE_RE`); it is **never** gated on the family enum. The daily index fetch loads the static config blacklist once per run (daily only), filters parsed cards in a single pass **after** sentinel accounting and **before** `select_index_entries`, and logs aggregate exclusion counts. CSV rows, `ReportMovies`, and history continue to use the existing `video_code` contract only.

**Tech Stack:** Python 3.11 dataclasses, Rust/PyO3 parser models, GitHub Actions config rendering, `pytest`, `cargo test`.

---

## Safety Notes

- **Recognition is additive.** Do not replace or narrow `_is_plausible_video_code` / `is_plausible_video_code`. Keep the existing logic verbatim and only add the western special-case. A regression here rejects real codes such as `259LUXU-1234`, `H4610-ki220101`, `1pondo-010120_001` and causes worse drift than the original bug.
- Do not edit or regenerate `reports/D1/d1_port_summary.json`.
- Do not add a D1 table, repo, ops CLI, or schema-version bump. The blacklist source is static config only (ADR-044 D3/D4 revision).
- Do not persist `video_code_family` to CSV rows, `ReportMovies`, `MovieHistory`, or `TorrentHistory`.
- Do not apply the family blacklist to ad hoc ingestion, even though ad hoc parsing receives the same `video_code_family` field.
- Apply the filter as a single pre-selection pass per page — not inside `select_index_entries` (which runs twice per page).

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/parsing/common.py` | Modify | Add `classify_video_code_family` + `WESTERN_STUDIO_DATE_RE`; widen `_is_plausible_video_code` additively. |
| `javdb/parsing/models.py` | Modify | Add `video_code_family` to `MovieIndexEntry`; legacy dict stays unchanged. |
| `javdb/parsing/fallback/index_parser.py` | Modify | Populate `video_code_family` when Rust is unavailable. |
| `javdb/rust_core/src/scraper/common.rs` | Modify | Rust classifier + dotted western token support; widen `is_plausible_video_code` additively. |
| `javdb/rust_core/src/scraper/index_parser.rs` | Modify | Populate `MovieIndexEntry.video_code_family`. |
| `javdb/rust_core/src/models.rs` | Modify | Expose `video_code_family` through PyO3 getters and `to_dict`; legacy dict stays unchanged. |
| `javdb/pipeline/index_family_blacklist.py` | Create | Config-only blacklist normalization + single-pass family filter with aggregate counts. |
| `javdb/spider/fetch/index.py` | Modify | Load daily blacklist once (daily only), filter parsed cards before selection, log aggregate stats. |
| `javdb/spider/fetch/index_parallel.py` | Modify | Same daily filter and stats behavior for parallel index fetch. |
| `javdb/spider/runtime/config.py` | Modify | Add `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST` normalized default. |
| `config.py.example` | Modify | Add the documented static default list. |
| `javdb/infra/config_generator.py` | Modify | Render `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST` from `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON`. |
| `.github/workflows/DailyIngestion.yml` | Modify | Surface `VAR_DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` for production daily runs. |
| `.github/workflows/TestIngestion.yml` | Modify | Surface the same variable for the daily-like smoke path. |
| `CONTEXT.md` | Modify | Add domain vocabulary for video code families and the daily index family blacklist. |
| `docs/handbook/en/self-hoster/configuration.md` | Modify | Document the new config key. |
| `docs/handbook/zh/self-hoster/configuration.md` | Modify | Chinese mirror of the config key. |
| `docs/handbook/en/self-hoster/github-actions-setup.md` | Modify | Document the GitHub Variable override. |
| `docs/handbook/zh/self-hoster/github-actions-setup.md` | Modify | Chinese mirror of the GitHub Variable override. |
| `tests/unit/test_parser.py` | Modify | Classifier families, additive-plausibility regression guard, and daily/ad hoc filter tests. |
| `tests/unit/test_api_parsers.py` | Modify | API parser field-shape coverage. |
| `tests/unit/test_api_models.py` | Modify | Model serialization and legacy-dict guard. |
| `tests/unit/test_index_family_blacklist.py` | Create | Normalization + single-pass filter + count tests. |

---

## Task 1: Parser contract - Python (additive)

**Files:**
- Modify: `javdb/parsing/common.py`
- Modify: `javdb/parsing/models.py`
- Modify: `javdb/parsing/fallback/index_parser.py`
- Test: `tests/unit/test_parser.py`
- Test: `tests/unit/test_api_parsers.py`
- Test: `tests/unit/test_api_models.py`

- [x] **Step 1: Write failing tests for classification, the additive-plausibility guard, and the model field.**

Add to `tests/unit/test_parser.py`:

```python
@pytest.mark.parametrize(
    ("code", "family"),
    [
        ("ABC-123", "classic_hyphenated"),
        ("FC2-PPV-1234567", "multi_hyphen"),
        ("062216-179", "numeric_date_hyphen"),
        ("062216_001", "numeric_date_underscore"),
        ("n0656", "hyphenless_studio"),
        ("Wifey.2026.05.30", "western_studio_date"),
        ("RKPrime.26.05.28", "western_studio_date"),
        # Real codes that match NO family but MUST stay valid video codes:
        ("259LUXU-1234", ""),
        ("H4610-ki220101", ""),
        ("1pondo-010120_001", ""),
    ],
)
def test_classify_video_code_family(code, family):
    from javdb.parsing.common import classify_video_code_family
    assert classify_video_code_family(code) == family


@pytest.mark.parametrize(
    "code",
    ["ABC-123", "259LUXU-1234", "H4610-ki220101", "1pondo-010120_001",
     "n0656", "Wifey.2026.05.30", "RKPrime.26.05.28"],
)
def test_extract_video_code_stays_additive(code):
    """Recognition is additive: every code above must parse to a non-empty
    video_code. The western dotted tokens are NEWLY accepted; the rest must
    NOT regress."""
    html = f'<a class="box" href="/v/x"><div class="video-title"><strong>{code}</strong> Title</div></a>'
    a_tag = BeautifulSoup(html, "html.parser").find("a")
    assert extract_video_code(a_tag) == code


def test_parse_index_page_sets_western_family():
    html = """
    <div class="movie-list"><div class="item">
      <a class="box" href="/v/wifey">
        <div class="video-title"><strong>Wifey.2026.05.30</strong> Title</div>
        <div class="tags has-addons"><span class="tag">CnSub DL</span><span class="tag">Today</span></div>
      </a>
    </div></div>
    """
    result = parse_index_page(html, page_num=1)
    assert result.movies[0].video_code == "Wifey.2026.05.30"
    assert result.movies[0].video_code_family == "western_studio_date"
```

Add to `tests/unit/test_api_models.py`:

```python
def test_movie_index_entry_family_serializes_but_legacy_dict_does_not():
    entry = MovieIndexEntry(
        href="/v/wifey",
        video_code="Wifey.2026.05.30",
        video_code_family="western_studio_date",
        page=2,
    )
    assert entry.to_dict()["video_code_family"] == "western_studio_date"
    assert "video_code_family" not in entry.to_legacy_dict()
```

Run:

```bash
pytest tests/unit/test_parser.py::TestExtractVideoCode tests/unit/test_parser.py::TestParseIndex tests/unit/test_api_models.py::TestMovieIndexEntry -v
```

Expected: `test_classify_video_code_family` and `test_parse_index_page_sets_western_family` fail (classifier/field missing); the western cases of `test_extract_video_code_stays_additive` fail (dotted tokens rejected today); the non-western cases of that test should already pass (proving they must not regress).

- [x] **Step 2: Add the classifier and widen plausibility additively.**

In `javdb/parsing/common.py`, define the family patterns once (the western regex requires at least one letter in the studio segment, so digit-prefixed studios like `21Sextury` match while a pure-numeric dotted token does not):

```python
VIDEO_CODE_FAMILIES = (
    "classic_hyphenated",
    "multi_hyphen",
    "numeric_date_hyphen",
    "numeric_date_underscore",
    "hyphenless_studio",
    "western_studio_date",
)

WESTERN_STUDIO_DATE_RE = re.compile(
    r"^[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*\.(?:\d{4}|\d{2})\.\d{2}\.\d{2}$"
)

_VIDEO_CODE_FAMILY_PATTERNS = (
    ("western_studio_date", WESTERN_STUDIO_DATE_RE),
    ("multi_hyphen", re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$")),
    ("numeric_date_hyphen", re.compile(r"^\d{6}-\d+$")),
    ("numeric_date_underscore", re.compile(r"^\d{6}_\d+$")),
    ("classic_hyphenated", re.compile(r"^[A-Za-z]+-\d+[A-Za-z0-9]*$")),
    ("hyphenless_studio", re.compile(r"^[A-Za-z]+\d+$")),
)


def classify_video_code_family(raw: str) -> str:
    """Return the recognized index-card video-code family label, or ''.

    This is a read-only labeller. It MUST NOT be used to decide whether a token
    is a valid video_code (see ``_is_plausible_video_code``): many real codes
    (e.g. ``259LUXU-1234``) match no family but are still valid.
    """
    s = (raw or "").strip()
    if len(s) < 2:
        return ""
    for family, pattern in _VIDEO_CODE_FAMILY_PATTERNS:
        if pattern.fullmatch(s):
            return family
    return ""
```

Then widen `_is_plausible_video_code` **additively** — keep the existing body verbatim and add only the western special-case at the top (dotted tokens otherwise fail the compact-token guard):

```python
def _is_plausible_video_code(raw: str) -> bool:
    """Heuristic: accept classic ``ABC-123`` codes, multi-hyphen codes
    (``FC2-PPV-1234567``), numeric date-style uncensored codes whether hyphen-
    or underscore-separated (``062216-179`` / ``062216_001``), hyphen-less
    studio codes (``n0656``), and dotted western studio/date tokens
    (``Wifey.2026.05.30``).

    Rejects empty strings, digit-less blobs, and title text (any character
    outside ``[A-Za-z0-9_-]`` plus the western exception, notably whitespace).
    """
    s = (raw or '').strip()
    if len(s) < 2:
        return False
    # Additive: dotted western tokens fail the compact-token guard below
    # because of '.', so accept them explicitly. This is the ONLY family that
    # needs special plausibility handling; the rest already pass the guard.
    if WESTERN_STUDIO_DATE_RE.fullmatch(s):
        return True
    if not all((c.isascii() and c.isalnum()) or c in '-_' for c in s):
        return False
    if not any(c.isdigit() for c in s):
        return False
    return any(c.isalpha() for c in s) or '-' in s or '_' in s
```

Update the `extract_video_code()` docstring to mention dotted western tokens. Add `classify_video_code_family` and `VIDEO_CODE_FAMILIES` to `__all__`.

> **Decoupling check:** `_is_plausible_video_code` references only `WESTERN_STUDIO_DATE_RE`, never `classify_video_code_family`. Do not change this — the classifier must not gate plausibility.

- [x] **Step 3: Add `video_code_family` to the Python model without changing legacy rows or positional construction.**

In `javdb/parsing/models.py`, append the field after the existing constructor
parameters so positional calls like `MovieIndexEntry("/v/x", "ABC-123", "Title")`
still set `title`; new callers should pass `video_code_family` by keyword:

```python
@dataclass
class MovieIndexEntry:
    """One movie card as it appears on any listing / index page."""
    href: str
    video_code: str
    title: str = ""
    rate: str = ""
    comment_count: str = ""
    release_date: str = ""
    tags: List[str] = field(default_factory=list)
    cover_url: str = ""
    page: int = 1
    ranking: Optional[int] = None
    video_code_family: str = ""
```

`to_dict()` uses `asdict(self)` and picks the field up automatically. Do **not** add the field to `to_legacy_dict()`.

- [x] **Step 4: Populate the family in the Python fallback parser.**

In `javdb/parsing/fallback/index_parser.py`, import `classify_video_code_family` alongside the other `javdb.parsing.common` imports. After `video_code = extract_video_code(a)`, add:

```python
    video_code_family = classify_video_code_family(video_code)
```

and pass `video_code_family=video_code_family` into the `MovieIndexEntry(...)` constructor.

- [x] **Step 5: Run Python parser/model tests.**

```bash
pytest tests/unit/test_parser.py::TestExtractVideoCode tests/unit/test_parser.py::TestParseIndex tests/unit/test_api_parsers.py tests/unit/test_api_models.py::TestMovieIndexEntry -v
```

Expected: all pass, including the additive-plausibility regression guard.

- [x] **Step 6: Commit the Python parser contract.**

```bash
git add javdb/parsing/common.py javdb/parsing/models.py javdb/parsing/fallback/index_parser.py tests/unit/test_parser.py tests/unit/test_api_parsers.py tests/unit/test_api_models.py
git commit -m "feat(parser): classify index video code families (additive)"
```

## Task 2: Parser contract - Rust parity (additive)

**Files:**
- Modify: `javdb/rust_core/src/scraper/common.rs`
- Modify: `javdb/rust_core/src/scraper/index_parser.rs`
- Modify: `javdb/rust_core/src/models.rs`

- [x] **Step 1: Add Rust tests for western parsing and the additive guard.**

In `javdb/rust_core/src/scraper/common.rs` tests, add:

```rust
#[test]
fn test_classify_video_code_family() {
    assert_eq!(classify_video_code_family("Wifey.2026.05.30"), "western_studio_date");
    assert_eq!(classify_video_code_family("RKPrime.26.05.28"), "western_studio_date");
    assert_eq!(classify_video_code_family("ABC-123"), "classic_hyphenated");
    // No family, but must still be a valid video code (see plausibility test):
    assert_eq!(classify_video_code_family("259LUXU-1234"), "");
}

#[test]
fn test_extract_video_code_is_additive() {
    for code in ["Wifey.2026.05.30", "259LUXU-1234", "H4610-ki220101", "1pondo-010120_001"] {
        let doc = Html::parse_document(&index_card(
            "/v/x",
            &format!("<div class=\"video-title\"><strong>{}</strong> Title</div>", code),
        ));
        let a = Selector::parse("a.box").unwrap();
        let el = doc.select(&a).next().unwrap();
        assert_eq!(extract_video_code(&el), code, "regressed code: {}", code);
    }
}
```

Run:

```bash
cd javdb/rust_core
cargo test scraper::common
```

Expected: fail because the Rust classifier does not exist and dotted tokens are rejected.

- [x] **Step 2: Implement the Rust classifier and widen plausibility additively.**

Add regexes near the existing static regexes (the western regex requires a letter in the studio segment, matching Python):

```rust
static WESTERN_STUDIO_DATE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*\.(?:\d{4}|\d{2})\.\d{2}\.\d{2}$").unwrap());
static MULTI_HYPHEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$").unwrap());
static NUMERIC_DATE_HYPHEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\d{6}-\d+$").unwrap());
static NUMERIC_DATE_UNDERSCORE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\d{6}_\d+$").unwrap());
static CLASSIC_HYPHENATED_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z]+-\d+[A-Za-z0-9]*$").unwrap());
static HYPHENLESS_STUDIO_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z]+\d+$").unwrap());
```

Then add the classifier:

```rust
pub fn classify_video_code_family(raw: &str) -> &'static str {
    let s = raw.trim();
    if s.chars().count() < 2 {
        return "";
    }
    if WESTERN_STUDIO_DATE_RE.is_match(s) {
        return "western_studio_date";
    }
    if MULTI_HYPHEN_RE.is_match(s) {
        return "multi_hyphen";
    }
    if NUMERIC_DATE_HYPHEN_RE.is_match(s) {
        return "numeric_date_hyphen";
    }
    if NUMERIC_DATE_UNDERSCORE_RE.is_match(s) {
        return "numeric_date_underscore";
    }
    if CLASSIC_HYPHENATED_RE.is_match(s) {
        return "classic_hyphenated";
    }
    if HYPHENLESS_STUDIO_RE.is_match(s) {
        return "hyphenless_studio";
    }
    ""
}
```

Widen `is_plausible_video_code` **additively** — keep the existing body and add only the western special-case at the top:

```rust
fn is_plausible_video_code(raw: &str) -> bool {
    let s = raw.trim();
    if s.len() < 2 {
        return false;
    }
    // Additive: dotted western tokens fail the compact-token guard below.
    if WESTERN_STUDIO_DATE_RE.is_match(s) {
        return true;
    }
    if !s
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return false;
    }
    if !s.chars().any(|c| c.is_ascii_digit()) {
        return false;
    }
    let has_letter = s.chars().any(|c| c.is_ascii_alphabetic());
    has_letter || s.contains('-') || s.contains('_')
}
```

> Do **not** rewrite `is_plausible_video_code` as `!classify_video_code_family(raw).is_empty()`. That is the regression D2 forbids.

- [x] **Step 3: Expose `video_code_family` on the Rust model.**

In `javdb/rust_core/src/models.rs`, add the field after `video_code`:

```rust
    #[pyo3(get, set)]
    pub video_code_family: String,
```

Keep the struct field immediately after `video_code`, but preserve the existing PyO3 constructor positional order. Append optional `video_code_family=String::new()` after the existing constructor parameters (after `ranking=None`) so calls like `RustMovieIndexEntry("/v/x", "ABC-123", "Title")` still set `title`; new callers can also pass it by keyword. Add to `to_dict()` only:

```rust
dict.set_item("video_code_family", &self.video_code_family)?;
```

Do **not** add it to `to_legacy_dict()`.

- [x] **Step 4: Populate `video_code_family` in the Rust index parser.**

In `javdb/rust_core/src/scraper/index_parser.rs`, import `classify_video_code_family` from `crate::scraper::common`. After `let video_code = extract_video_code(&a);`, add:

```rust
    let video_code_family = classify_video_code_family(&video_code).to_string();
```

and set `video_code_family` in the `MovieIndexEntry { ... }` struct literal.

- [x] **Step 5: Run Rust tests.**

```bash
cd javdb/rust_core
cargo test
```

Expected: all Rust tests pass.

- [x] **Step 6: Rebuild the extension and commit.**

```bash
cd javdb/rust_core && maturin develop --release && cd ../../..
git add javdb/rust_core/src/scraper/common.rs javdb/rust_core/src/scraper/index_parser.rs javdb/rust_core/src/models.rs
git commit -m "feat(parser): add rust video code family parity (additive)"
```

## Task 3: Daily-only config-driven family blacklist

**Files:**
- Create: `javdb/pipeline/index_family_blacklist.py`
- Modify: `javdb/spider/fetch/index.py`
- Modify: `javdb/spider/fetch/index_parallel.py`
- Test: `tests/unit/test_index_family_blacklist.py`

- [x] **Step 1: Write failing tests for normalization and the single-pass filter.**

Create `tests/unit/test_index_family_blacklist.py`:

```python
from __future__ import annotations

from javdb.parsing.models import MovieIndexEntry
from javdb.pipeline.index_family_blacklist import (
    normalize_family_blacklist,
    filter_blacklisted_families,
)


def _entry(code, family):
    return MovieIndexEntry(href=f"/v/{code}", video_code=code, video_code_family=family)


def test_normalize_trims_and_drops_empties():
    assert normalize_family_blacklist([" western_studio_date ", "", None]) == {"western_studio_date"}


def test_normalize_empty_opts_out():
    assert normalize_family_blacklist([]) == set()
    assert normalize_family_blacklist(None) == set()


def test_filter_drops_blacklisted_and_counts_once():
    movies = [
        _entry("Wifey.2026.05.30", "western_studio_date"),
        _entry("ABC-123", "classic_hyphenated"),
        _entry("259LUXU-1234", ""),
    ]
    counts = {}
    kept = filter_blacklisted_families(movies, {"western_studio_date"}, counts)

    assert [m.video_code for m in kept] == ["ABC-123", "259LUXU-1234"]
    assert counts == {"western_studio_date": 1}


def test_filter_empty_blacklist_keeps_everything():
    movies = [_entry("Wifey.2026.05.30", "western_studio_date")]
    counts = {}
    assert filter_blacklisted_families(movies, set(), counts) == movies
    assert counts == {}
```

Run:

```bash
pytest tests/unit/test_index_family_blacklist.py -v
```

Expected: fail because the module does not exist.

- [x] **Step 2: Implement the config-only blacklist helpers.**

Create `javdb/pipeline/index_family_blacklist.py`:

```python
"""Daily index video-code-family blacklist (config-only, ADR-044).

The blacklist is sourced entirely from the static
``DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST`` config list. There is no D1 control
plane: video-code families are a closed enum defined by the parser.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)


def normalize_family_blacklist(values: Iterable[str] | None) -> set[str]:
    """Trim and drop empties, returning the active family set."""
    return {str(v).strip() for v in (values or []) if str(v).strip()}


def filter_blacklisted_families(movies, blacklist, counts=None):
    """Return movies whose ``video_code_family`` is not in ``blacklist``.

    Runs as a single pre-selection pass (ADR-044 D3/D5), so each excluded card
    is counted exactly once. ``counts`` accumulates per-family exclusion totals.
    """
    if not blacklist:
        return movies
    kept = []
    for m in movies:
        family = getattr(m, "video_code_family", "") or ""
        if family and family in blacklist:
            if counts is not None:
                counts[family] = counts.get(family, 0) + 1
            continue
        kept.append(m)
    return kept
```

- [x] **Step 3: Wire the daily filter into the sequential fetch.**

In `javdb/spider/fetch/index.py`, import:

```python
from javdb.infra.logging import log_summary_block
from javdb.pipeline.index_family_blacklist import (
    normalize_family_blacklist,
    filter_blacklisted_families,
)
```

At the start of `_fetch_all_index_pages_sequential()` (near `_sentinel_field_health.start_run()`), initialize the blacklist once — daily only:

```python
    family_blacklist_counts: dict[str, int] = {}
    daily_family_blacklist: set[str] = set()
    if custom_url is None:
        from javdb.spider.runtime.config import DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST
        daily_family_blacklist = normalize_family_blacklist(DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST)
```

Immediately **after** the sentinel observe block (so the sentinel still sees the raw parsed cards) and **before** the phase-1/phase-2 `select_index_entries` calls, add:

```python
        if daily_family_blacklist:
            page_result.movies = filter_blacklisted_families(
                page_result.movies, daily_family_blacklist, family_blacklist_counts
            )
```

Before the function returns, log the aggregate only when non-empty:

```python
    if family_blacklist_counts:
        pairs = [("total", sum(family_blacklist_counts.values()))]
        pairs.extend(sorted(family_blacklist_counts.items()))
        log_summary_block(logger, "INDEX FAMILY BLACKLIST SUMMARY", pairs)
```

> `select_index_entries` is **not** modified. The filter runs before it, so both phase calls operate on the already-filtered `page_result.movies` and exclusions are counted once. `is_adhoc_mode=(custom_url is not None)` already keeps ad hoc runs unaffected because the blacklist set is empty for them.

- [x] **Step 4: Mirror the wiring in the parallel fetch.**

In `javdb/spider/fetch/index_parallel.py`, apply the same imports, the same daily-only blacklist initialization, the same post-`observe` / pre-`select` filter call against each page's `page_result.movies`, and the same end-of-run aggregate `log_summary_block`. Use a thread-safe accumulation if pages are filtered inside worker threads (e.g. accumulate per-page counts and merge under the existing results lock, or filter on the main thread after each page result is collected).

- [x] **Step 5: Run blacklist tests.**

```bash
pytest tests/unit/test_index_family_blacklist.py -v
```

Expected: pass.

- [x] **Step 6: Commit the daily filter.**

```bash
git add javdb/pipeline/index_family_blacklist.py javdb/spider/fetch/index.py javdb/spider/fetch/index_parallel.py tests/unit/test_index_family_blacklist.py
git commit -m "feat(pipeline): filter daily index families (config-only)"
```

## Task 4: Config and workflow wiring

**Files:**
- Modify: `javdb/spider/runtime/config.py`
- Modify: `config.py.example`
- Modify: `javdb/infra/config_generator.py`
- Modify: `.github/workflows/DailyIngestion.yml`
- Modify: `.github/workflows/TestIngestion.yml`

- [x] **Step 1: Add the runtime config default (normalized to a clean list).**

In `javdb/spider/runtime/config.py`, near the phase settings:

```python
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = cfg(
    "DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST",
    ["western_studio_date"],
)
if DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST is None:
    DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = []
elif not isinstance(DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST, (list, tuple, set)):
    DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = [DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST]
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = [
    str(item).strip()
    for item in DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST
    if str(item).strip()
]
```

- [x] **Step 2: Add the default to `config.py.example`.**

In the Spider Configuration section after `PHASE2_MIN_COMMENTS`:

```python
# Daily-only index video-code-family blacklist.
# Defaults to excluding western studio/date cards from daily ingestion while
# the parser still recognizes them and ad-hoc ingestion still uses them.
# Set to [] to opt out of the western default.
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = ['western_studio_date']
```

- [x] **Step 3: Render the key from GitHub Variables.**

In `javdb/infra/config_generator.py`, add a config-map entry in the Spider Configuration section:

```python
(
    'DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST',
    'DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON',
    get_env_json,
    ['western_studio_date'],
    'SPIDER CONFIGURATION',
),
```

- [x] **Step 4: Add workflow env wiring for daily-like runs.**

In `.github/workflows/DailyIngestion.yml`, inside the `Generate config.py from GitHub Variables and Secrets` step env block:

```yaml
          VAR_DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON: ${{ vars.DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON || '["western_studio_date"]' }}
```

Add the same line to the config-generator step in `.github/workflows/TestIngestion.yml`.

`AdHocIngestion.yml` needs no change: even if it renders the key via defaults, the fetch layer only loads the blacklist when `custom_url is None`, so ad hoc runs never apply it.

- [x] **Step 5: Verify config rendering.**

```bash
VAR_DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON='["western_studio_date"]' \
python3 -m apps.cli.ops.config_generator --dry-run
```

Expected: output includes `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = ["western_studio_date"]`.

- [x] **Step 6: Commit config and workflow wiring.**

```bash
git add javdb/spider/runtime/config.py config.py.example javdb/infra/config_generator.py .github/workflows/DailyIngestion.yml .github/workflows/TestIngestion.yml
git commit -m "ci(workflows): wire daily index family blacklist config"
```

## Task 5: Documentation follow-through

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/handbook/en/self-hoster/configuration.md`
- Modify: `docs/handbook/zh/self-hoster/configuration.md`
- Modify: `docs/handbook/en/self-hoster/github-actions-setup.md`
- Modify: `docs/handbook/zh/self-hoster/github-actions-setup.md`

- [x] **Step 1: Add domain language to `CONTEXT.md`.**

Insert near the parsing / filter vocabulary (no D1 table is involved):

```md
### Video code family / Daily index family blacklist

ADR-044 adds a parser-classified `video_code_family` label to each index card
(`classic_hyphenated`, `multi_hyphen`, `numeric_date_hyphen`,
`numeric_date_underscore`, `hyphenless_studio`, `western_studio_date`). The
label is classification metadata only — it never decides whether a token is a
valid `video_code`. Daily ingestion excludes families listed in
`DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST` (default `['western_studio_date']`) in
a single pre-selection pass, after the site-contract sentinel has observed the
raw parsed cards. Ad hoc ingestion bypasses the blacklist.
```

- [x] **Step 2: Document the config key (en + zh).**

Add to `docs/handbook/en/self-hoster/configuration.md` under Spider configuration:

```md
| `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST` | `list[str]` | `['western_studio_date']` | Daily-only family blacklist applied after index parsing and sentinel accounting. The parser still recognizes these families, and ad-hoc ingestion bypasses this blacklist. In GitHub Actions, set repo Variable `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` to a JSON array such as `[]` to opt out of the static default. |
```

Mirror in `docs/handbook/zh/self-hoster/configuration.md` (preserve the config name, path, and default verbatim).

- [x] **Step 3: Document the GitHub Actions variable (en + zh).**

In `docs/handbook/en/self-hoster/github-actions-setup.md`, add to the non-sensitive variables table:

```md
| `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` | JSON array of video-code families excluded from daily ingestion, default `["western_studio_date"]`. Set to `[]` to stop excluding the western studio/date family. |
```

Mirror in the Chinese page.

- [x] **Step 4: Verify docs and formatting.**

```bash
rg -n "video code family|video_code_family|DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST" CONTEXT.md docs/handbook/en docs/handbook/zh
git diff --check
```

Expected: terms present in the intended docs; no whitespace errors.

- [x] **Step 5: Commit docs.**

```bash
git add CONTEXT.md docs/handbook/en/self-hoster/configuration.md docs/handbook/zh/self-hoster/configuration.md docs/handbook/en/self-hoster/github-actions-setup.md docs/handbook/zh/self-hoster/github-actions-setup.md
git commit -m "docs: document daily index family filtering"
```

## Task 6: End-to-end verification

**Files:** No new files. Validates the complete branch.

- [x] **Step 1: Run focused Python tests.**

```bash
pytest \
  tests/unit/test_parser.py \
  tests/unit/test_api_parsers.py \
  tests/unit/test_api_models.py \
  tests/unit/test_index_family_blacklist.py \
  -v
```

Expected: all pass — especially the additive-plausibility regression guard (`259LUXU-1234`, `H4610-ki220101`, `1pondo-010120_001` stay valid video codes).

- [x] **Step 2: Run Rust tests and rebuild.**

```bash
cd javdb/rust_core && cargo test && maturin develop --release && cd ../../..
```

Expected: all Rust parser/model tests pass and the wheel rebuilds.

- [x] **Step 3: Cross-engine parity check.**

```bash
python3 - <<'PY'
from javdb.parsing.common import classify_video_code_family, extract_video_code
import javdb.rust_core as rc  # optional: compare against the rust path if exposed
for code in ["Wifey.2026.05.30", "259LUXU-1234", "ABC-123", "H4610-ki220101"]:
    print(code, "->", classify_video_code_family(code))
PY
```

Expected: `Wifey.2026.05.30 -> western_studio_date`, `259LUXU-1234 ->` (empty), `ABC-123 -> classic_hyphenated`, `H4610-ki220101 ->` (empty).

- [x] **Step 4: Dry-run config rendering.**

```bash
VAR_DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON='["western_studio_date"]' \
python3 -m apps.cli.ops.config_generator --dry-run
```

Expected: output includes `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = ["western_studio_date"]`.

- [x] **Step 5: Final hygiene checks.**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files modified.

## Rollback

- Code rollback: revert the implementation commits in reverse order.
- Config rollback: set `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` to `[]` (or clear the config list) to stop excluding the western family. No D1 / schema rollback is needed — this feature touches no database schema.

## Out of Scope

- Any D1 control plane (`IndexFilterRule` table, repo, ops CLI, schema-version bump). Families are a closed parser-defined enum; revisit only if a concrete runtime-mutable need appears.
- Allowlist semantics for daily ingestion.
- A generalized parser taxonomy beyond the six families listed in ADR-044.
- Persisting `video_code_family` to CSV, reports, history, or uploader payloads.
- Applying the family blacklist to ad hoc ingestion.
- Editing generated report artifacts under `reports/`.
