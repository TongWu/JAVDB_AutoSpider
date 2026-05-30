# ADR-035: Site-Contract Drift Sentinel

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; execution delegated to per-phase IMPs            |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-020](../ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md), [ADR-019](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 2 — proactive reliability).

## Context

The entire system rests on javdb.com's HTML structure. The Rust scraper hardcodes
selectors (`div.item`, `a.box`, `div.video-title`, `div.score`, `span.value`,
`div.meta`, `span.tag`, …) in `javdb/rust_core/src/scraper/`. When the site
changes, parsing **silently degrades** — and today the system cannot see it:

- **`html_validators.py` only catches catastrophic failure** —
  `validate_index_html() -> (is_valid, is_empty)` detects an `empty-message` div,
  a login wall, or a maintenance page. It answers "is this a usable index page at
  all?", not "did a field quietly stop parsing?".
- **There is no field-level drift detection.** If `div.score` stops matching, the
  `score` field becomes `null` for 100% of items while everything else parses;
  the daily run completes, writes to the DB, and the data is silently wrong.
- **Golden fixtures exist but only for unit tests** —
  `tests/fixtures/parser/*.html` (7 files) are offline edge-case regressions, not
  a live drift baseline.
- **`health_check.py` checks infrastructure only** (qB / proxy / SMTP), never
  parser/site health.

The failure mode that matters — and is wholly uncovered — is **silent,
field-level parser drift**: a structurally-valid page whose individual fields
have quietly collapsed. [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
is *reactive* (post-failure diagnosis); this ADR is *proactive* — detect the
drift, and stop it polluting the DB, on the run it first appears.

## Decision

Build a **site-contract drift sentinel**: a declarative per-field parse contract,
two observation sources (per-run piggyback telemetry + a small independent
canary) feeding one detection core, and a **tiered action** — critical drift
gates the commit, soft drift raises an advisory — reusing the existing
pending→commit lifecycle and the ADR-026 incident surface.

### Design Decisions

**D1. Hybrid observation: piggyback telemetry + independent canary, one core.**
Two sources feed one `detectors` core: (a) the existing daily parse path is
instrumented to emit **per-field fill-rate per run** (no new fetch), and (b) a
small independent **canary** periodically fetches a fixed page set and parses it.
Source (a) catches drift on the first daily run after a site change; source (b)
catches it *between* runs, before the next daily run.

**D2. A declarative parse contract with severity tiers + layered detection.**
A `PARSE_CONTRACT` (`javdb/spider/parse_contract.py`) declares, per
`page_type × field`, a `severity` (`critical` | `soft`) and an expectation:

```python
PARSE_CONTRACT = {
  "index": {
    "href":        {"severity": "critical", "min_fill": 0.99},
    "video_code":  {"severity": "critical", "min_fill": 0.99},
    "title":       {"severity": "critical", "min_fill": 0.95},
    "score":       {"severity": "soft",     "baseline_rel": 0.5},
    "comments":    {"severity": "soft",     "baseline_rel": 0.5},
  },
  "detail": {
    "magnets":     {"severity": "critical", "min_fill": 0.90},
    "actors":      {"severity": "soft",     "baseline_rel": 0.5},
    "release_date":{"severity": "soft",     "baseline_rel": 0.5},
  },
}
```

- **Critical** field: `fill_rate < min_fill` → critical drift (absolute threshold;
  these must be near-100% or the product is broken).
- **Soft** field: `fill_rate < baseline_rel × rolling_baseline` → soft drift
  (relative collapse; self-calibrating, low false-positive).
- **Sample-size guard:** skip evaluation when the observed item count is below a
  floor (avoid flapping on tiny runs).

**D3. Tiered action — critical gates the commit, soft warns.** Evaluated against
the run's field-health just before session commit:

- **Critical drift →** the session is **not committed**: marked `failed`
  (`FailureReason='site_drift'`), pending rows are not promoted (existing
  failed-session cleanup path), and a **critical OpsIncident + email advisory** is
  raised. `MovieHistory` / `TorrentHistory` are protected from silent garbage.
- **Soft drift →** a soft OpsIncident + email advisory; the run commits normally.

**D4. Boundary with `html_validators` — drift ≠ catastrophe.** The sentinel fires
**only** when a page is structurally a valid index/detail but a field collapsed.
Login walls, maintenance pages, and empty results remain `html_validators`'
domain (a different failure mode), so the sentinel never double-alerts or
false-flags a login wall as "drift".

**D5. Baseline-erosion mitigation (boiling-frog).** A soft field that slowly
decays must not drag its own baseline down until alerting stops. Mitigations:
(a) a **slow EMA** baseline; (b) **freeze a field's baseline update once it is in
soft-drift** until acknowledged; (c) **learn the baseline only from clean
(non-drift), committed runs**.

**D6. Reuse the ADR-026 incident surface — the sentinel is a new detector.**
Drift events are written as `OpsIncidents` with a new `incident_type='site_drift'`
and flow through the existing email advisory path. No new alerting system; the
ADR-026 AI diagnosis can summarize drift incidents like any other.

**D7. Module shape mirrors ADR-033 / ADR-026.** `javdb/ops/sentinel/` holds
`service.py` (`run(SentinelOptions) -> SentinelResult`), `contract.py`,
`detectors.py`, `probes.py` (canary), `field_health.py` (piggyback aggregation),
`models.py`, `persistence.py`. `apps/cli/ops/sentinel.py` is the CLI adapter;
`.github/workflows/SiteContractSentinel.yml` schedules the canary; the piggyback
hook lives at the parse boundary; the commit gate lives at the session-commit
path.

**D8. Data model: one baseline table; no new alerting table.**
`ParseFieldHealth` (per `page_type × field`: rolling baseline fill-rate, last
fill-rate, last_observed_at, sample_count) is the D1-canonical baseline store.
Drift events reuse `OpsIncidents` (D6).

## Consequences

### Positive

- **Silent field-level drift becomes visible** — the wholly-uncovered failure
  mode is now detected, on the first run it appears.
- **Bad data is stopped at the gate** — critical drift refuses the commit instead
  of writing silent garbage, reusing pending→commit.
- **Proactive lead time** — the canary flags "the site changed; the next daily
  run will fail" between runs.
- **No new alerting surface** — reuses ADR-026 incidents + email.
- **Self-calibrating** — soft thresholds track a learned baseline, not magic
  numbers.

### Negative

- **A contract to maintain** — critical/soft classification and thresholds are
  hand-authored and must evolve with the parser.
- **Tuning risk** — too-tight thresholds gate good runs; the sample-size guard and
  relative baselines mitigate but do not eliminate this.
- **The canary adds fetch/Cloudflare/login cost** (Phase 2) — kept tiny by design.
- **Commit-gate coupling** — the gate sits in the session-commit path and must
  stay aligned with the ADR-019 lifecycle.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Piggyback + gate | [IMP-ADR035-01](IMP-ADR035-01-piggyback-and-gate.md) | `parse_contract`; `field_health` per-run telemetry; `detectors`; `ParseFieldHealth` baseline; tiered action (critical gate + soft incident) on the daily run; `site_drift` incident type | Independent canary; web/AI surface |
| Phase 2 — Independent canary | IMP-ADR035-02 (stub) | `probes` + `SiteContractSentinel.yml` cron + pinned pages + golden anchors; between-run detection | — |
| Phase 3 — Surface (optional) | IMP-ADR035-03 (stub) | Per-field health on web (ADR-034 pattern) / AI drift summary | — |

Phase 1 delivers the headline value (catch drift + protect the DB) with **zero new
fetch**. Phase 2 adds between-run lead time. Phase 3 is optional polish.

### Explicit non-goals (YAGNI)

- **No selector self-heal / auto-rewrite of the parser** — too risky; the sentinel
  detects and gates, a human fixes the selector.
- **No ML** — fill-rate vs. baseline is deterministic and explainable.
- **The canary stays tiny** — one index page + a handful of pinned detail pages.
- **No duplicate catastrophic detection** — login/maintenance/empty stay with
  `html_validators` (D4).

## Domain Language (additions for CONTEXT.md)

- **Parse contract** — the declarative per-`(page_type, field)` spec of severity
  and expected fill, the source of truth for "what a healthy parse looks like".
- **Field fill-rate** — fraction of parsed items where a field is non-empty; the
  drift metric.
- **Site drift** — a structurally-valid page whose individual fields have
  collapsed below contract/baseline; `critical` (gates) or `soft` (warns).
- **Sentinel** — the service that evaluates field-health against the contract.
- **Canary probe** — the small independent scheduled fetch+parse used for
  between-run drift detection.

## Alternatives Considered

- **Warn-only on all drift** — rejected (D3): lets silent garbage land in the DB;
  the whole point is to stop critical drift at the gate.
- **Strict gate on any drift** — rejected (D3): a single soft field stops the
  pipeline; high false-positive/operational cost.
- **Canary-only or piggyback-only** — rejected (D1): piggyback alone has no
  between-run lead time; canary alone misses the full daily sample and adds the
  most Cloudflare cost for the least coverage.
- **Fully-learned contract (no hand-authored severities)** — rejected (D2): a
  learned baseline cannot know which fields are *critical enough to gate*; the
  critical/soft split must be declared.

## References

- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-020 — Parser Interface Consolidation](../ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md)
- [ADR-019 — Session Lifecycle Authority](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-011 — Parsing Module](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
