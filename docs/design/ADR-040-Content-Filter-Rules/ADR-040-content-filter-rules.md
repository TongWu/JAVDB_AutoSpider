# ADR-040: Content Filter Rules

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted — Phase 1 implemented; later phases pending                  |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md), [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) |

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
exclude all-male). **Age is deferred (Phase 2)** — it needs an actor-profile
lookup the pipeline does not do today. **Subscriptions (whitelist that bypasses the
rating threshold) are deferred (Phase 2)** — they are the include counterpart and
a larger change.

**D6. Deterministic and explainable; orthogonal to the preference model.** The
engine returns a `FilterDecision(keep, reasons)`; drop reasons are surfaced (stats
/ a `MovieFiltered` event / MCP). This is a **hard, deterministic rules** layer —
distinct from the **ML preference score** of
[ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) /
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
  Phase 2.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Exclude + attribute | [IMP-ADR040-01](IMP-ADR040-01-content-filter.md) | `ContentFilterRule` table + repo; `content_filter` engine (actor blacklist, tag include/exclude, gender); post-detail filter stage; a CLI to manage rules | age; subscriptions; web/MCP management |
| Phase 2 — Subscriptions + age | IMP-ADR040-02 (stub) | D1 subscriptions (whitelist bypassing the rating threshold); age filter via actor-profile enrichment; web/MCP rule management | — |
| Phase 3 — Compose (optional) | IMP-ADR040-03 (stub) | combine with the ADR-025 preference score | — |

Phase 1 is additive and backward-compatible (no rules → no change). Phases 2/3
widen the include side and attribute coverage.

### Explicit non-goals (YAGNI)

- **No streaming / frequent cron** — the pivot; the daily cadence stays.
- **No age filter in Phase 1** — needs actor-profile enrichment (Phase 2).
- **No subscriptions in Phase 1** — the include/whitelist side is Phase 2.
- **No ML** — deterministic rules only; preference scoring is ADR-022/025.
- **No rewrite of the rating/rater filter** — a parallel second gate (D3).

## Domain Language (additions for CONTEXT.md)

- **Content filter rule** — a row in `ContentFilterRule`: a dimension (actor/tag/
  gender), a mode (exclude/include/…), and a value.
- **Blacklist** — exclude-mode content filter rules (highest precedence).
- **Attribute filter** — a rule on a parsed attribute (gender, tag).
- **Filter decision** — the engine's `keep` + `reasons` for one movie.
- **Subscription** — (Phase 2) a followed entity whose new releases bypass the
  rating threshold.

## Alternatives Considered

- **Streaming / frequent polling** — rejected (D1): the original framing, but slow
  release velocity makes it pointless; filtering, not freshness, is the value.
- **Rules in `config.py` only** — rejected (D2): static; D1 enables runtime/web/MCP
  management.
- **ML-only filtering (lean on ADR-022/025)** — rejected (D6): a hard blacklist is
  deterministic and immediate; the preference model is a separate, later ranking
  concern.

## References

- [ADR-022 — User Preference Data Foundation](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [ADR-038 — Agentic Operator MCP Surface](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)

## Status Log

- 2026-05-29: Proposed (pivoted from "streaming ingestion" to content filtering;
  three phases scoped, IMPs pending).
- 2026-05-30: Phase 1 implemented via [IMP-ADR040-01](IMP-ADR040-01-content-filter.md);
  ADR remains active for Phase 2/3.
