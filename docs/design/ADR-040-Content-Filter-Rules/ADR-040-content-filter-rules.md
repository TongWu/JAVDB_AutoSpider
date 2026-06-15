# ADR-040: Content Filter Rules

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted — Phase 1-2 implemented; Phase 3 superseded by ADR-054 WS2; later phases pending |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md), [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) |

> Originated from a 2026-05-29 brainstorming session that started as "streaming /
> continuous ingestion" (Direction 7) and **pivoted** to content filtering — see
> Context.

## Context

The daily ingestion (`DailyIngestion.yml`, cron `00 12 * * *`) scrapes the
homepage new releases once a day and selects entries by **rating and rater count**
(quantity/popularity signals). This brainstorm began as "streaming/continuous
ingestion" — more frequent polling for fresher results.

**That framing was wrong for this system, and the design pivoted.** Film release
velocity is slow: a daily run yields fewer than ~50 films. Frequent polling buys
nothing; the existing daily cadence (or even less) is sufficient. The real,
operator-stated gap is **filtering power**: the current rating/rater-count filter
cannot exclude or include by **identity or attribute** — there is no way to
blacklist specific actors or tags, or to filter by lead/all-actor gender. (Age
filtering was raised too, but actor age is **not on the movie detail page** — it
requires an actor-profile lookup — so it is deferred.)

This ADR therefore augments daily ingestion with a **content filter rule layer**:
deterministic identity/attribute include/exclude rules applied as an additional
gate. No streaming, no new frequent cron.

## Decision

Add a D1-backed `ContentFilterRule` layer and a deterministic filter stage that
runs **after detail parse, before queueing to qBittorrent**, AND-ed with the
existing rating/rater filter. Phase 1 covers the dimensions obtainable from the
existing detail parse — actor blacklist, tag include/exclude, gender — with a
blacklist-wins precedence. Age and subscriptions are deferred.

### Design Decisions

**D1. Pivot recorded: content filtering, not streaming.** Frequent polling is
explicitly rejected — slow release velocity makes the daily cadence sufficient.
The value is filtering, not freshness. (The "streaming" framing is retired; this
ADR replaces it.)

**D2. A dynamic `ContentFilterRule` D1 table.** Rules live in D1 so they are
manageable at runtime (later via web/MCP), not hardcoded in `config.py`:

```sql
CREATE TABLE ContentFilterRule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  dimension  TEXT NOT NULL,   -- actor | tag | gender
  mode       TEXT NOT NULL,   -- exclude | include | require_lead | exclude_all_male ...
  value      TEXT,            -- actor name/href | tag | gender value
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);
```

**D3. A new filter stage after detail parse, before the qB queue.** Identity and
attribute data (actors, gender, tags) are only available **after** the detail page
is parsed, so the content filter runs there — downstream of the existing
index-stage rating/rater filter, which is unchanged. A movie must pass **both**
gates to be queued.

**D4. Precedence: blacklist wins; rules AND together.** Any matching **exclude**
rule drops the movie immediately. Remaining **include/attribute** rules are AND-ed
(e.g. a tag-include set requires at least one matching tag; a gender rule requires
the configured condition). The content filter is AND-ed with the existing rating
filter — neither weakens the other.

**D5. Phase 1 dimensions come from the existing parse.** From `MovieDetail`
(`actors` with name/href/**gender**, `tags`): **actor blacklist** (exclude by
name/href), **tag include/exclude**, **gender** (e.g. require a female lead,
exclude all-male). **Age is Phase 2 (IMP-ADR040-02).** Correction to the original Context aside:
javdb's own `/actors/<id>` page is a movie *listing* page and carries **no
birthdate**, so age cannot come from a javdb lookup. Phase 2 instead resolves
birthdates **best-effort from minnano-av** (matched by actor name), cached in
`ActorMetadata`, computing age at the movie's release date. Actors with no
resolved birthdate have unknown age and never cause a drop. (xslist was weighed as
a fallback but deferred — it cannot match javdb's Japanese names.)
**Subscriptions are superseded by [ADR-054 WS2](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md)**:
the rating-threshold bypass is now part of one unified Subscription domain
(`ActorSubscription` + `NewWorks`) rather than an ADR-040-only whitelist. The
bypass is pinned by `tests/unit/test_adhoc_bypasses_rating_gate.py`.

**D6. Deterministic and explainable; orthogonal to the preference model.** The
engine returns a `FilterDecision(keep, reasons)`; drop reasons are surfaced (stats
/ a `MovieFiltered` event / MCP). This is a **hard, deterministic rules** layer —
distinct from the **ML preference score** of
[ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) /
[ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md). The
two are orthogonal: rules decide *eligibility*, the model later decides *ranking*.

**D7. Module shape.** `javdb/spider/services/content_filter.py` (sibling to
`dedup.py`) exposes `evaluate(detail, rules) -> FilterDecision`; a
`ContentFilterRepo` reads the rules; the detail-selection path calls it before
queueing.

## Consequences

### Positive

- **Precise ingestion** — blacklist unwanted actors/tags; require a gender
  condition; include only chosen tags. The stated gap is closed.
- **Dynamic** — rules in D1, manageable at runtime (web/MCP later), not a config
  redeploy.
- **Explainable** — every drop has a reason; nothing disappears silently.
- **Additive & safe** — a second gate AND-ed with the unchanged rating filter.
- **Right-sized** — no streaming machinery the release velocity does not justify.

### Negative

- **Another gate to reason about** — operators must understand blacklist-wins +
  AND precedence.
- **Attribute coverage is parser-bound** — gender/tags only; age needs the deferred
  actor-profile enrichment.
- **Rule-management surface** — Phase 1 manages rules via CLI; web/MCP management is
  Phase 4.

## Implementation Roadmap

| Phase | IMP | Ships |
| --- | --- | --- |
| Phase 1 — Exclude + attribute | IMP-ADR040-01 (done) | actor/tag/gender rules |
| Phase 2 — Age filter | IMP-ADR040-02 (done) | `age` dimension; external-source enrichment (minnano-av; xslist deferred); `ActorMetadata` cache |
| Phase 2b — Regex + release-date | IMP-ADR040-03 (done) | `regex_exclude`/`regex_include` (actor/tag); `release_date` `before`/`after`; no schema migration (reuses the generic triple) |
| Phase 3 — Subscriptions | Superseded by [ADR-054 WS2](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md) | unified actor subscriptions + new-works feed; rating threshold bypass occurs by reusing the AdHoc scrape path (no ADR-040 bypass code), pinned by `tests/unit/test_adhoc_bypasses_rating_gate.py` |
| Phase 4 — Web/MCP rule mgmt | IMP-ADR040-04 (web CRUD done; MCP future) | dual-backend `/api/content-filter` REST CRUD + `content_filter` flag + Settings page + read-side Movies overlay; MCP still blocked on ADR-038 |
| Phase 5 — Compose (optional) | IMP-ADR040-05 (stub) | combine with the ADR-025 preference score |

Phase 1 is additive and backward-compatible (no rules → no change). Phase 2
widens attribute coverage. The former Phase 3 is no longer an ADR-040 phase; it
is owned by ADR-054 WS2 so "Subscription" has one domain meaning.

> **IMP-number note (WS4a, 2026-06-15):** the ADR-054 User-Intent campaign
> repurposed the IMP-ADR040-03 / -04 numbers — **IMP-ADR040-03 is now the
> regex/release-date engine** (Phase 2b above) and **IMP-ADR040-04 is the
> web-CRUD surface** (Phase 4). The legacy "Subscriptions" idea once sketched as
> IMP-ADR040-03 has been **relocated to ADR-054 WS2** (the Phase 3 row above now
> records that supersede); the `(stub)` label on Phase 5 predates this and is
> reconciled by its new owner.

### Explicit non-goals (YAGNI)

- **No streaming / frequent cron** — the pivot; the daily cadence stays.
- **No age filter in Phase 1** — needs actor-profile enrichment (Phase 2).
- **No subscriptions in Phase 1** — the include/whitelist side was deferred and
  is now superseded by ADR-054 WS2.
- **No ML** — deterministic rules only; preference scoring is ADR-022/025.
- **No rewrite of the rating/rater filter** — a parallel second gate (D3).

## Domain Language (additions for CONTEXT.md)

- **Content filter rule** — a row in `ContentFilterRule`: a dimension (actor/tag/
  gender/age/release_date), a mode (exclude/include/regex_exclude/regex_include/
  require_lead/exclude_all_male/min_age/max_age/before/after), and a value.
- **Regex rule** — a `regex_exclude`/`regex_include` rule whose value is a Python
  `re.search` pattern; a bad pattern fails open (never drops, never raises).
- **Release-date rule** — a `release_date` `before`/`after` rule comparing the
  movie's parsed `release_date` to an ISO bound; an absent/unparseable date never
  drops.
- **Blacklist** — exclude-mode content filter rules (highest precedence).
- **Attribute filter** — a rule on a parsed attribute (gender, tag).
- **Filter decision** — the engine's `keep` + `reasons` for one movie.
- **Subscription** — superseded by ADR-054 WS2: a followed entity whose new
  releases surface in a New-Works feed and bypass the rating threshold through
  the AdHoc scrape path; the bypass is a property of `is_adhoc_mode` selection,
  not a new index-gate hook.

## Alternatives Considered

- **Streaming / frequent polling** — rejected (D1): the original framing, but slow
  release velocity makes it pointless; filtering, not freshness, is the value.
- **Rules in `config.py` only** — rejected (D2): static; D1 enables runtime/web/MCP
  management.
- **ML-only filtering (lean on ADR-022/025)** — rejected (D6): a hard blacklist is
  deterministic and immediate; the preference model is a separate, later ranking
  concern.

## References

- [ADR-022 — User Preference Data Foundation](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [ADR-038 — Agentic Operator MCP Surface](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)

## Status Log

- 2026-05-29: Proposed (pivoted from "streaming ingestion" to content filtering;
  three phases scoped, IMPs pending).
- 2026-05-30: Phase 1 implemented via [IMP-ADR040-01](IMP-ADR040-01-content-filter.md);
  ADR remains active for Phase 2/3.
- 2026-06-04: Phase-2 scope corrected — javdb actor pages have no birthdate; age
  filtering uses minnano-av (best-effort, by name), age computed at release date.
  Roadmap re-numbered (age=Phase 2; subscriptions=Phase 3; web/MCP=Phase 4).
  Planned in [IMP-ADR040-02](IMP-ADR040-02-age-filter.md).
- 2026-06-07: Phase 2 implemented via [IMP-ADR040-02](IMP-ADR040-02-age-filter.md)
  (PR #180) — `age` dimension, minnano-av enrichment, `ActorMetadata` cache. ADR
  remains active for Phases 3-5.
- 2026-06-15: Content-filter engine extended with **regex** (`regex_exclude` /
  `regex_include` on actor/tag) and **release-date** (`release_date` dimension,
  `before` / `after`) modes via [IMP-ADR040-03](IMP-ADR040-03-content-filter-regex-date.md).
  Both reuse the generic `(dimension, mode, value)` triple with **no schema
  migration** — the Phase-2 (age) no-migration template. Engine + CLI only
  ([MAIN]); the dual-backend web CRUD `/api/content-filter` + SPA Settings/overlay
  surface remain [IMP-ADR040-04](IMP-ADR040-04-content-filter-web-crud.md).
- 2026-06-15: Former Phase 3 "Subscriptions" superseded by
  [ADR-054 WS2](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md).
  WS2 defines the single Subscription domain (`ActorSubscription` + `NewWorks`)
  and reuses the AdHoc scrape path, whose phase-2 selection bypasses the
  rating/rater threshold by construction. ADR-040 no longer owns subscription
  bypass code; the behavior is pinned by
  `tests/unit/test_adhoc_bypasses_rating_gate.py`.
- 2026-06-15: Phase 4 (web CRUD) shipped via [IMP-ADR040-04](IMP-ADR040-04-content-filter-web-crud.md):
  a dual-backend `/api/content-filter` CRUD API (Python FastAPI router delegating to
  `ContentFilterRepo`; TS Hono Worker re-implementing the same SQL), a `content_filter`
  capability flag (both backends probe `ContentFilterRule` in **REPORTS_DB**), a
  `SettingsFilterRulesPage.vue` CRUD table, and a read-side Movies overlay that dims
  rule-matched rows (ADR-054 D6 — presentation only). **No schema migration.** The
  `(dimension, mode)` allow-list stays canonical in the CLI, imported by the Python router,
  hand-mirrored in TS, and pinned by a cross-backend parity golden — reconciled to the 13/12
  pairs that include the IMP-03 regex/release_date modes. The web boundary validates
  `release_date` (strict ISO) but **not** regex compile-ability: JS `new RegExp` and Python
  `re` dialects diverge (inline flags like `(?i)` throw in JS), so the engine's fail-open is
  the authoritative guard. MCP management remains future (ADR-038). ADR stays active for Phase 5.
