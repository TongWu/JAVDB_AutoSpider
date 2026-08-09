# ADR-044: Index Video Code Family Blacklist

| Field | Value |
| --- | --- |
| **Status** | Completed — implemented and verified on 2026-06-01 |
| **Date** | 2026-06-01 |
| **Authors** | Ted |
| **Related** | [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-040](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md), [ADR-042](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) |
| **Related Implementation Plans** | [IMP-ADR044-01](IMP-ADR044-01-index-video-code-family-blacklist.md) |

> This ADR came out of a drift investigation: the parser must recognize a new index-card family so the sentinel sees a filled `video_code`, but daily ingestion must still keep that family out of the download queue by default.

## Context

The parser already distinguishes these page types: `index`, `detail`, `actors`, `makers`, `publishers`, `series`, `directors`, `video_codes`, `search`, `tags`, `top250`, `top_movies`, `top_playback`, and `unknown`. This ADR is narrower than the whole parser surface: it only adds a classification label to index-card `video_code` tokens and changes how daily ingestion treats one newly-recognized family.

In the failure case (run [26716551239](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26716551239)), nine cards out of a 400-card daily sample carried a western studio/date first-token (e.g. `Wifey.2026.05.30`, `RKPrime.26.05.28`). The parser's `_is_plausible_video_code` guard rejects any token containing characters outside `[A-Za-z0-9_-]` — the dots made these tokens fail, so they were left with an empty `video_code`. That dropped `index.video_code` fill enough to trip the critical contract sentinel (ADR-035), even though the page content was not actually missing data. At the same time, those cards are intentionally outside the daily ingestion target set, so simply widening the parser without a filter would let them drift into the queue.

This is a boundary problem, not just a parsing bug:

1. The parser should recognize the family so downstream code can reason about it explicitly — **without narrowing the set of tokens already accepted as valid `video_code`s**.
2. Daily ingestion should exclude that family by default.
3. Ad hoc ingestion should keep its current behavior and not inherit the daily blacklist.

ADR-040 already introduced a separate detail-page content filter. That layer is the wrong shape for this problem: it runs after detail parse and operates on actors/tags/gender, not on index-card video-code families.

## Decision

Add an explicit, **additive** `video_code_family` classification to index-page movie entries and use a daily-only, **config-driven** family blacklist to exclude selected families from ingestion.

### Design Decisions

**D1. Classify index-card video codes with an additive classifier.**

`MovieIndexEntry` gains a stable `video_code_family` field. A new `classify_video_code_family()` returns one of the recognized family labels, or an empty string when the token matches none of them.

Recognized families are:

- `classic_hyphenated` — e.g. `ABC-123`
- `multi_hyphen` — e.g. `FC2-PPV-1234567`
- `numeric_date_hyphen` — e.g. `062216-179`
- `numeric_date_underscore` — e.g. `062216_001`
- `hyphenless_studio` — e.g. `n0656`
- `western_studio_date` — e.g. `Wifey.2026.05.30`, `RKPrime.26.05.28` (covers both `Studio.YYYY.MM.DD` and `Studio.YY.MM.DD`)

The family label is **classification metadata only**. Of the six families, only `western_studio_date` is consumed (by the daily blacklist in D3); the others exist so the field is a complete, honest taxonomy and so future filters can reference them. A token that matches no family (e.g. a prefix-number studio code like `259LUXU-1234`) gets an empty `video_code_family` but **remains a fully valid `video_code`** (see D2).

**D2. Recognition is additive — never narrow `video_code` plausibility.**

This is the load-bearing constraint of the whole ADR. The existing `_is_plausible_video_code` heuristic (Python and Rust) accepts a broad space of compact tokens: ASCII alphanumerics with `-`/`_` separators, at least one digit, and either a letter or a separator. That space includes many real JavDB codes the six families do **not** match — prefix-number studio codes (`259LUXU-1234`, `300MIUM-0571`, `200GANA-…`), Heydouga-style codes (`H4610-ki220101`), and underscore-mixed codes (`1pondo-010120_001`).

Therefore the parser change is strictly additive:

- Keep the existing permissive plausibility check exactly as-is.
- Widen it **only** to also accept dotted western tokens (which currently fail because `.` is outside the allowed character set).
- `classify_video_code_family()` is a **separate, read-only labeller**. It must never be used to decide whether a token is a valid `video_code`. Tying plausibility to the family enum would reject the broad classes above and cause far worse drift — and silent download loss — than the original nine cards.

Python and Rust must apply the same additive change so the two engines stay in agreement.

**D3. The family blacklist is daily-only, config-driven, and applied as an independent pre-selection step.**

The blacklist applies only to `DailyIngestion`. `AdHocIngestion` bypasses it (it already passes `is_adhoc_mode=True` down to selection).

The daily pipeline order is:

```text
parse index cards -> sentinel accounting -> daily family blacklist (independent step) -> phase 1 / phase 2 selection
```

Order matters: the sentinel must observe the family as a filled `video_code` (it observes the raw parsed cards before any filtering), while daily ingestion must drop the family before it becomes a download candidate. The blacklist is a **distinct pass over the parsed cards**, run once per page after sentinel accounting and before the phase-1/phase-2 selection calls — not folded into `select_index_entries` (which runs twice per page and would otherwise double-count exclusions and entangle family logic with phase logic).

The blacklist source is **static config only**:

```python
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = ["western_studio_date"]
```

In GitHub Actions this is rendered from the repo Variable `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` (JSON array). Operators opt out of the western default by clearing the config list (or setting the Variable to `[]`). There is no D1 control plane (see Alternatives): video-code families are a **closed enum defined by the parser** — you cannot blacklist a family the parser does not emit, and adding a new family already requires a code change, so the natural place to also adjust the blacklist is config in that same change.

**D4. Keep the family field out of CSV, report, and history rows.**

`video_code_family` is part of the parser contract and the daily selection/debug surface, but it does not become a persisted report/download field. Daily CSV rows, `ReportMovies`, `MovieHistory`, and `TorrentHistory` stay on the existing `video_code` contract. It is exposed in `MovieIndexEntry.to_dict()` (debug/API) but **not** in `to_legacy_dict()`.

**D5. Emit family-level statistics, counted once per page.**

Daily runs report family-level exclusion counts — a total plus a per-family breakdown such as `western_studio_date=9`. Because the blacklist runs as a single pre-selection pass (D3), each excluded card is counted exactly once. This explains why candidates vanished without flooding the log with per-card records.

## Domain Language

- **Video code family** — a parser-classified label for an index-card `video_code` token. Classification metadata only; never gates `video_code` validity.
- **Daily index family blacklist** — the set of families excluded only from daily ingestion, sourced from static config.
- **Western studio/date family** — the new western-style token family (`Studio.YYYY.MM.DD` / `Studio.YY.MM.DD`) that stays excluded by default; family label `western_studio_date`.

## Consequences

### Positive

- **Sentinel stays honest** — recognized western-family cards now count as filled `video_code` values.
- **No regression** — recognition is additive, so every token the parser accepts today still parses; only dotted western tokens are newly accepted.
- **Daily ingestion stays scoped** — the new family is recognized but still excluded by default.
- **The contract is explicit** — downstream code can inspect `video_code_family` instead of guessing from string shape.
- **Ad hoc behavior stays intact** — the blacklist does not leak into custom runs.
- **Minimal surface** — one config key, one parser label, one pre-selection filter; no new table, repo, CLI, or schema migration.

### Negative

- **Parser/model surface grows** — `MovieIndexEntry` and its debug/API serialization gain one field; the classifier carries six regexes (five of which are currently unused labels).
- **Config-only means deploy-to-change** — adding a future family to the blacklist requires editing config / the GitHub Variable rather than a runtime command. This is acceptable because adding a family already requires a parser code change.

## Alternatives Considered

- **Keep the family implicit and lower the sentinel threshold** — rejected. That would hide a real parser/contract mismatch instead of fixing it.
- **Gate `video_code` plausibility on the family enum** (only accept the six families) — rejected, and explicitly forbidden by D2. It would reject common real codes (`259LUXU-1234`, `H4610-…`, `1pondo-…_…`), crashing fill rate and silently dropping legitimate downloads.
- **Reuse ADR-040 `ContentFilterRule`** — rejected. That filter runs after detail parse and operates on a different, open-ended data shape (actors/tags/gender), where runtime mutability genuinely pays off.
- **Add a D1 `IndexFilterRule` control plane (table + repo + ops CLI + schema bump)** — rejected for Phase 1. Video-code families are a closed enum defined by the parser, so runtime-mutable rules add a confusing config∪D1 merge rule and a three-DB schema-version bump for near-zero benefit. Revisit only if a concrete need for runtime-mutable family exclusion emerges.
- **Let ad hoc ingestion inherit the daily blacklist** — rejected. The user explicitly wants ad hoc runs to keep the broader parser acceptance without the daily exclusion policy.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR044-01](IMP-ADR044-01-index-video-code-family-blacklist.md) | additive parser family classification (Python + Rust); `video_code_family` field; widened plausibility for western tokens; config-driven daily-only family blacklist as a pre-selection step; config/workflow wiring; family-level stats | D1 runtime control plane; family allowlist semantics; any broader parser taxonomy; persisting the family field |

## References

- [ADR-035 — Site Contract Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-040 — Content Filter Rules](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md)
- [ADR-042 — D1 Atomic Commit Boundaries](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)
- [`javdb/parsing/common.py`](../../../../javdb/parsing/common.py)
- [`javdb/rust_core/src/scraper/common.rs`](../../../../javdb/rust_core/src/scraper/common.rs)
- [`javdb/pipeline/index_selection.py`](../../../../javdb/pipeline/index_selection.py)
- [`javdb/spider/fetch/index.py`](../../../../javdb/spider/fetch/index.py)
- [`javdb/spider/fetch/index_parallel.py`](../../../../javdb/spider/fetch/index_parallel.py)
- [`javdb/ops/sentinel/field_health.py`](../../../../javdb/ops/sentinel/field_health.py)
- [`javdb/infra/config_generator.py`](../../../../javdb/infra/config_generator.py)
- [`.github/workflows/DailyIngestion.yml`](../../../../.github/workflows/DailyIngestion.yml)
- [`.github/workflows/AdHocIngestion.yml`](../../../../.github/workflows/AdHocIngestion.yml)

## Status Log

- 2026-06-01: Accepted (initial) — parser recognition and daily-only blacklist boundaries agreed.
- 2026-06-01: Design review (grill) revised three decisions before implementation:
  - **D1/D2** — recognition made strictly additive; classification is decoupled from `video_code` plausibility (the original plan would have rejected common real codes such as `259LUXU-1234`).
  - **D3** — dropped the D1 `IndexFilterRule` control plane (table + repo + ops CLI + three-DB schema bump); the blacklist is now config-only, and is applied as an independent pre-selection step rather than inside `select_index_entries`.
  - **D5** — exclusion stats are counted once per page (a consequence of the pre-selection step).
