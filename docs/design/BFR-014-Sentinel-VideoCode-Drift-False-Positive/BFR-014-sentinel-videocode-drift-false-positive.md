# BFR-014: Sentinel video_code drift false-positive blocks daily commit

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/rust_core/src/scraper/common.rs`, `javdb/parsing/common.py`, `javdb/spider/parse_contract.py`, `javdb/ops/sentinel/`, `javdb/migrations/d1/2026_05_27_add_ops_incidents.sql`
**Related**: [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)

---

## Symptom

The daily ingestion `mark-sessions-as-committed` step failed with exit code 1
([run 26712478741](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26712478741)):

```text
✗ __main__  Site-contract drift gate: critical drift for session
            20260531T122613.805503Z-8382-0000 (1 finding(s)); refusing commit
            (FailureReason=site_drift).
Commit done: committed=0 already_committed_or_missing=0 failed=1
Error: Process completed with exit code 1.
```

Immediately above it, a swallowed warning:

```text
⚠ javdb.ops.se  evaluate_session: incident persist failed
javdb.storage.d1_client.D1PermanentError: D1 API returned HTTP 400:
  [{'code': 7500, 'message': 'no such table: OpsIncidents: SQLITE_ERROR'}]
```

The run's session was then torn down by on-failure cleanup; the day's data did
not commit. The ADR-035 sentinel was on its **first** real firing
(`ParseRunFieldFill` held only this one session; no committed baseline existed).

## Root Cause

Two independent defects compounded:

**1. (Primary — the commit blocker) The Rust index-card `extract_video_code`
diverged from the parse contract and dropped valid codes.** The sentinel
([javdb/ops/sentinel/field_health.py](../../../javdb/ops/sentinel/field_health.py))
observes the raw, pre-filter index cards (`page_result.movies`) and scores
`index.video_code` against an absolute `min_fill` of 0.99
([parse_contract.py](../../../javdb/spider/parse_contract.py)). Production parses
with the Rust core, whose `extract_video_code` validated a candidate with only
`if !video_code.contains('-') { return "" }`. That heuristic:

- dropped hyphen-less studio codes (`n0656`) and underscore date-style
  uncensored codes (`062216_001`) — both are valid JavDB codes; and
- on a card with no `<strong>`, returned the whole `"CODE Title"` blob.

On a normal daily listing ~4% of *real* movie cards (uncensored content) yield
an empty index code. The detail-page parse recovers the code, so `MovieHistory`
stays clean — but the sentinel measures the *raw index* fill, which fell to 0.96
< 0.99 and tripped the critical gate. The finding was therefore a **false
positive**: a too-strict, contract-divergent extractor combined with an
un-calibrated absolute threshold measured over cards the pipeline never commits.
(The Python fallback `_is_plausible_video_code` was *also* wrong but differently
— its single-hyphen regex rejected multi-hyphen FC2 codes.)

**2. (Secondary — diagnostic loss) The `OpsIncidents` D1 table never existed in
production.** Migration `2026_05_27_add_ops_incidents.sql` (ADR-026) was authored
but never applied to the `javdb-reports` D1 database (D1 increments are applied
by hand; a later migration landed while this one was missed). `evaluate_session`
caught the resulting error and downgraded it to a warning, so the drift incident
— the diagnostic record for this very gating event — was silently discarded.

## Fix

- **Parser plausibility, both engines aligned.** Replaced the hyphen-only check
  in Rust `extract_video_code`
  ([common.rs](../../../javdb/rust_core/src/scraper/common.rs)) and Python
  `_is_plausible_video_code` ([common.py](../../../javdb/parsing/common.py)) with
  a shared rule: a compact `[A-Za-z0-9_-]` token that contains a digit and
  either a letter or a `-`/`_` separator. The no-`<strong>` branch now keeps
  only the leading code token and drops the title. Accepts `ABC-123`,
  `FC2-PPV-1234567`, `062216-179`, `062216_001`, `n0656`; rejects title text.
  Rust + Python unit tests added; Rust wheel auto-rebuilds in CI on
  `javdb/rust_core/**` change.
- **Applied the missing migration** `2026_05_27_add_ops_incidents.sql` to the
  reports D1 so incidents persist going forward.
- **Backfilled** the 216 historical `ReportMovies.VideoCode` NULLs from the
  authoritative `MovieHistory` codes.

## Side Effects

- Raising index `video_code` fill (uncensored codes are no longer dropped) is
  the intended effect; the gate should now pass on normal runs. Behaviour for
  censored hyphenated codes is unchanged (no regression — verified end-to-end
  against the compiled Rust wheel).
- The no-`<strong>` branch now returns a code where it previously returned
  empty/garbage; real JavDB cards always have `<strong>`, so the practical delta
  is limited to malformed cards.

## Follow-Up

- [ ] Watch the next daily run's `index.video_code` fill; if it still sits below
      0.99 due to genuinely code-less cards, recalibrate the threshold rather
      than lowering the guard blindly.
- [ ] Wire detail-page observation so `detail.video_code` (critical 0.99)
      becomes an *active* guard — only `index` is observed today, so demoting
      the index threshold currently has no safety net.
- [ ] Consider measuring fill over *selected* entries or adding baseline-relative
      critical thresholds, so a newly-deployed gate cannot false-positive on its
      first firing with no baseline.
- [ ] (Separate) Clean up orphaned child rows from rolled-back sessions and fix
      the rollback cascade gap (tracked as its own task).
