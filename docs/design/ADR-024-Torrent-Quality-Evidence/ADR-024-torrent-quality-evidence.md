# ADR-024: Torrent Quality Evidence Foundation

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed                                                              |
| **Date**    | 2026-05-27                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) |

## Context

The current torrent selection path preserves four production categories:
`hacked_subtitle`, `hacked_no_subtitle`, `subtitle`, and `no_subtitle`.
Within each category, the best candidate is still largely inferred from JavDB
metadata, torrent name, publish time, and total torrent size. This keeps the
pipeline simple, but it misses several quality failures that only become
visible after qBittorrent receives torrent metadata:

- total torrent size can be inflated by advertising files, samples, screenshots,
  archives, or other non-primary-video content;
- JavDB category labels can be wrong, such as a torrent marked as subtitled
  without embedded subtitles or subtitle files, or a torrent marked as
  uncensored hacked while still being censored;
- torrent names can claim resolution or category attributes that the file list
  does not support;
- some video-level problems, such as watermarks, visual ads, or bad picture
  quality, need content inspection and are too risky for the first rollout.

Daily ingestion already downloads the production-selected torrent to the local
NAS through the production qBittorrent endpoint. Shadow quality probing must not
increase NAS storage pressure or feed candidate torrents into downstream
scraping, PikPak, file-filtering, or media-organizer flows. The system also has
to work across multiple qBittorrent endpoints: the production local NAS endpoint
and a remote server endpoint that can be used for short-lived metadata probes.

The opportunity is to build a D1-first evidence layer before introducing any ML
model. The first version should collect objective torrent metadata, calculate an
explainable score, and report what the ranker would have chosen without changing
the production download decision.

## Decision

Create a D1-canonical torrent quality evidence foundation that runs in shadow
mode first. It collects file-list evidence from both production downloads and
remote probe candidates, computes an explainable score, and stores structured
decisions for later assist/enforce rollout or offline model training.

ADR-024 does not introduce model inference in the production path. Phase 1 is
rule-based, auditable, and shadow-only.

### Design Decisions

D1. **Preserve production category semantics** - The production pipeline keeps
the existing four torrent categories. Phase 1 evaluates candidates within those
categories but does not replace the production-selected magnet.

D2. **D1 is the source of truth** - Torrent quality evidence and evaluation rows
are created by D1 migrations first. SQLite may mirror the tables for local
debugging, but local-only evidence is not authoritative.

D3. **Separate torrent evidence from movie-context evaluation** - Objective
file-list facts are keyed by `info_hash` and `probe_schema_version`. Contextual
evaluation is keyed by `info_hash`, movie context, and `scoring_version` because
the same torrent can appear under multiple JavDB pages or inferred categories.

D4. **Collect from two qBittorrent roles** - The production qBittorrent endpoint
provides evidence for the torrent that DailyIngestion already downloads to the
local NAS. The remote `quality_probe` endpoint provides short-lived evidence for
shadow candidates. Evidence rows record `target_role` values such as
`production_download` and `quality_probe`.

D5. **Never treat probe candidates as production downloads** - Shadow candidates
must use a dedicated qBittorrent category, for example `JavDB Quality Shadow`,
on the remote probe endpoint. Downstream file filter, PikPak bridge, scraper,
and organizer flows must ignore that category by default.

D6. **Probe candidates are short-lived** - A probe candidate is added only long
enough to receive metadata and file-list evidence. After collection, the torrent
is removed with `deleteFiles=false`. Timeout cases are also removed and recorded
with statuses such as `pending_timeout`.

D7. **Metadata-only probing must be capability detected** - Plain `paused=true`
may prevent magnet metadata from being fetched, depending on qBittorrent
behavior. The implementation must probe the target endpoint for a metadata-only
flow, preferably `stopCondition=MetadataReceived` where supported. If the target
does not support safe metadata probing, shadow probing fails closed and records
`probe_capability_unsupported` without affecting production ingestion.

D8. **Top-K shadow collection is bounded** - Phase 1 collects evidence for the
current production-selected torrent plus a bounded Top K per category, with a
global cap per run. This avoids full magnet fan-out while still revealing cases
where the total-size heuristic would have chosen poorly.

D9. **Explainability is part of the contract** - Scores must be decomposed into
structured signals and reasons, not stored as an opaque number. Reason codes
include examples such as `junk_ratio_high`, `subtitle_file_missing`,
`main_video_detected`, `category_mismatch`, `resolution_claim_unsupported`, and
`probe_unavailable`.

D10. **No video content inspection in Phase 1** - The first version uses JavDB
static metadata and qBittorrent file-list metadata only. Frame capture, OCR,
watermark detection, visual ad detection, and censored/uncensored visual
classification are deferred to a future ADR or later phase.

D11. **Shadow-only before assist/enforce** - Phase 1 writes evidence, computes
scores, and reports the candidate that would have won. It does not change the
production uploader. Later phases may introduce `TORRENT_QUALITY_POLICY_MODE`
values such as `shadow`, `assist`, and `enforce`.

### Evidence Model

`TorrentQualityEvidence` stores torrent-level objective facts:

- `info_hash`, `probe_schema_version`, `target_role`, `probe_target_name`;
- `metadata_status`, `metadata_started_at`, `metadata_completed_at`;
- `total_size_bytes`, `main_video_size_bytes`, `main_video_ratio`;
- `video_file_count`, `subtitle_file_count`, `non_video_file_count`;
- `junk_size_bytes`, `junk_size_ratio`, `suspicious_file_count`;
- summarized file-list features and reason codes;
- safe magnet/source fingerprints, not raw secrets.

`TorrentQualityEvaluation` stores movie-context scoring facts:

- `info_hash`, `movie_href`, `video_code`, `javdb_category`;
- `magnet_name`, `javdb_tags_json`, `javdb_size_text`;
- inferred category and category consistency signals;
- subtitle evidence, resolution consistency, and source trust signals;
- `score`, `shadow_rank`, `would_replace_current_choice`;
- `decision` values such as `accepted_shadow`, `rejected_shadow`,
  `needs_review`, `probe_unavailable`;
- `reasons_json` and `scoring_version`.

The exact table names can change during implementation, but the split between
torrent-level evidence and context-level evaluation is part of this decision.

### Scoring Signals

Phase 1 scoring is intentionally explainable:

- effective main-video size, not raw total torrent size;
- junk/ad-file ratio penalty;
- subtitle file evidence, with name-only subtitle hints treated as weak signals;
- JavDB category, magnet name, and file-name consistency;
- claimed resolution versus file-name and category hints;
- abnormal file count and suspicious extension patterns;
- freshness as a tie-breaker rather than the dominant quality signal.

The score is only a shadow score until later rollout gates enable behavior
changes.

### qBittorrent Isolation

Production downloads and probes use different qBittorrent roles:

| Role | Endpoint | Category | Behavior |
| --- | --- | --- | --- |
| `production_download` | Existing local NAS qBittorrent | Existing production category | Read evidence only; do not delete or recategorize |
| `quality_probe` | Dedicated remote qBittorrent | `JavDB Quality Shadow` | Metadata-only probe; remove after file-list collection |

If `quality_probe` is not configured, cannot log in, lacks metadata-only
capability, or times out, the system records evidence status and continues the
production pipeline unchanged.

## Consequences

### Positive

- The system can distinguish useful video size from inflated torrent size.
- JavDB category mistakes become measurable instead of hidden in names/tags.
- Evidence is reusable by `info_hash` across duplicate magnet URLs.
- Production NAS pressure stays bounded because shadow probes run on a remote
  endpoint and are removed quickly.
- The first rollout remains safe because it only observes and reports.
- The collected D1 dataset can later support threshold tuning, supervised
  learning, or learning-to-rank without changing the initial schema direction.

### Negative

- Phase 1 adds another qBittorrent integration role and operational config.
- Some qBittorrent versions may not support a clean metadata-only probe path.
- Metadata fetches can still consume remote bandwidth and tracker resources.
- File-list signals cannot detect visual watermarks, embedded ads, or true video
  quality by themselves.

### Risks

- **Probe endpoint accidentally triggers downstream automation** - Mitigation:
  use a dedicated category and make downstream jobs ignore it by default.
- **Paused torrents never fetch metadata** - Mitigation: capability-detect
  `stopCondition=MetadataReceived` or equivalent behavior before enabling probe
  collection.
- **Evidence rows drift from scoring logic** - Mitigation: version both evidence
  extraction (`probe_schema_version`) and scoring (`scoring_version`).
- **Overconfidence in rule scores** - Mitigation: keep Phase 1 shadow-only and
  expose reason codes before enabling assist/enforce modes.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | Future IMP | D1 evidence schema, production/probe evidence collection, bounded Top-K shadow scoring, qB capability canary, logs/API report | No production download behavior change |
| Phase 2 | Future IMP | Assist mode that can recommend per-category replacements and surface review actions in API/Web | Fully automatic enforcement |
| Phase 3 | Future IMP | Enforce mode behind rollout gates, threshold tuning, backfill/reporting jobs | Video frame/CV inspection and heavyweight ML runtimes |

## References

- `javdb/spider/magnet_extractor.py` - current category extraction and size/time sort.
- `javdb/pipeline/policies.py` - current category semantics and missing-type policy.
- `javdb/integrations/qb/client.py` - shared qBittorrent add/delete/list client.
- `javdb/integrations/qb/file_filter.py` - current qBittorrent file-list access.
- `javdb/integrations/qb/uploader.py` - production qBittorrent category and upload path.
- [qBittorrent WebUI API 5.0](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29) - add, files, torrent state, and delete API behavior.
- [qBittorrent AddTorrentParams source](https://raw.githubusercontent.com/qbittorrent/qBittorrent/master/src/base/bittorrent/addtorrentparams.h) - server-side add-torrent parameters including stop condition support.
- [qbittorrent-api torrents docs](https://qbittorrent-api.readthedocs.io/en/v2025.5.0/apidoc/torrents.html) - client-surface reference for add-torrent options.

## Status Log

- 2026-05-27: Proposed as ADR-024.
