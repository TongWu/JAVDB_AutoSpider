# ADR-025: User Preference Model

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed                                                              |
| **Date**    | 2026-05-27                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md) |

## Context

[ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
created the data foundation for user preference modeling: `MovieMetadata`,
`MovieRatings`, and `ContentPreferences`. It intentionally left the model itself
as a deferred follow-up because useful model design depends on enough explicit
user ratings and preference annotations.

The first model should not try to become a broad recommendation brain for every
pipeline decision. Torrent quality is now owned by
[ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md),
and duplicate/novelty decisions already have deterministic history, inventory,
and dedup signals. Mixing those concerns into the first preference model would
make both training and explanation harder.

The goal of ADR-025 is to define the first preference model as a structured,
explainable `preference_score` that can later participate in a larger
`download_utility_score` without controlling production ingestion by default.

## Decision

Build an offline-trained, D1-canonical, explainable user preference model that
predicts `preference_score` from ADR-022 data. The first version uses explicit
ratings and content preferences as strong signals, treats implicit behavior as
weak side-channel evidence, persists versioned predictions, and exposes results
to Web/API and pipeline shadow/assist flows.

ADR-025 replaces the deferred model placeholder referenced by ADR-022.

### Design Decisions

D1. **Optimize download utility, implement preference first** - The long-term
target is `download_utility_score`, but Phase 1 implements only
`preference_score`. `quality_score` remains owned by ADR-024, and
`novelty_or_redundancy_score` starts as deterministic policy.

D2. **Use explicit ratings as strong labels** - `MovieRatings` entries,
including 1-5 ratings and explicit tags, are the primary labels. Explicit
dimension preferences in `ContentPreferences` are strong features and may become
label priors. Download completion, retention, dedup, re-download, and manual
actions are weak signals only.

D3. **Keep the first model structured and explainable** - The first trainable
model should be regularized regression, ordinal regression, gradient boosted
trees, or another lightweight structured model. Online LLM inference is not part
of the hot path.

D4. **Do not train before enough data exists** - Continue using versioned
rule-based scoring until at least 200 explicit movie ratings exist. More complex
models should wait for more data, for example 500+ explicit ratings.

D5. **Feature inputs come from ADR-022 data first** - Phase 1 features are
limited to `MovieMetadata`, `MovieRatings`, and `ContentPreferences`: actors,
maker, publisher, director, series, category, tags, JavDB score, want/seen
counts, release recency, user ratings, and dimension preferences.

D6. **Implicit behavior is a side channel** - Retention, deletion, dedup, manual
re-download, and downstream scraping results may be stored as
`implicit_signal_summary` or used for backtesting, but they do not drive the
core Phase 1 training labels.

D7. **Persist predictions, do not compute them in request hot paths** - Web/API
and pipeline consumers read `MoviePreferencePredictions` from D1. Training and
batch prediction run offline through CLI or workflow entry points.

D8. **Model artifacts live outside D1 blobs** - D1 stores model registry
metadata, metrics, status, and artifact URI. The artifact itself is a JSON model
artifact in object storage, GitHub Actions artifact storage, or another
repo-external artifact location. Very small JSON rule artifacts may be in D1 if
needed, but D1 is not designed as a model blob store.

D9. **Candidate models can coexist, production reads one primary** - Model
registry rows may have statuses such as `candidate`, `primary`, `archived`, and
`failed`. Production consumers read exactly one primary model per policy scope,
while candidates can generate shadow predictions for comparison.

D10. **Promotion requires gates and human confirmation** - A candidate model
must pass offline metrics, time-split validation, ranking-quality checks,
calibration checks, regression protection, and shadow disagreement review before
an explicit CLI/API operation promotes it to primary.

D11. **Pipeline consumption is gated** - Web/API may display scores directly,
but ingestion starts with `PREFERENCE_POLICY_MODE=shadow` or `assist`. Automatic
skips or priority changes require a later `enforce` rollout gate.

### Target Function

The system-level utility can be represented as:

```text
download_utility_score =
  combine(preference_score,
          quality_score,
          novelty_or_redundancy_score,
          policy_constraints)
```

ADR-025 owns only `preference_score`.

- `preference_score`: Does the movie match the user's content preferences?
- `quality_score`: Is the available torrent good enough? Owned by ADR-024.
- `novelty_or_redundancy_score`: Is this new or useful relative to history,
  inventory, and dedup state? Initially rule-based.
- `policy_constraints`: Storage, category, safety, and operator-controlled
  gates.

### Label Strategy

The normalized training target is `utility_label` in `[0, 1]`, but Phase 1 maps
it mainly from explicit preference data:

- 1-5 `MovieRatings` map to ordered preference labels.
- Explicit like/dislike tags and dimension-level `hearted` preferences enrich
  the label and feature context.
- Implicit behavior may fill gaps only as weak evidence and must be marked as
  lower confidence.

The first trainable model should be evaluated with time-based splits instead of
random splits, because the production problem is future prediction from past
ratings.

### Prediction Contract

Each prediction must be explainable enough for Web UI, API clients, and pipeline
logs:

- `score`;
- `confidence`;
- `model_version`;
- `feature_schema_version`;
- `top_positive_reasons_json`;
- `top_negative_reasons_json`;
- `feature_group_scores_json`;
- `computed_at`;
- `input_snapshot_hash`.

Example reason groups include actors, maker, publisher, director, series, tags,
category, JavDB rating, and explicit user preferences.

### Data Model

`MoviePreferencePredictions` stores versioned predictions:

- `movie_href`, `video_code`;
- `model_version`, `feature_schema_version`;
- `score`, `confidence`;
- `top_positive_reasons_json`, `top_negative_reasons_json`;
- `feature_group_scores_json`;
- `input_snapshot_hash`, `computed_at`;
- `prediction_status`, for example `ready`, `missing_inputs`, or `stale`.

`PreferenceModelRegistry` stores model metadata:

- `model_version`, `policy_scope`;
- `status`, for example `candidate`, `primary`, `archived`, or `failed`;
- `algorithm`, `feature_schema_version`;
- `trained_at`, `training_data_cutoff`;
- `artifact_uri`, `artifact_sha256`;
- `metrics_json`, `promotion_notes_json`.

D1 is the canonical source. SQLite mirrors the tables for local debugging only.

### Training And Serving Flow

Training and prediction are offline operations:

1. Extract training rows from D1.
2. Build feature vectors using the versioned feature schema.
3. Train a candidate model or versioned rule artifact.
4. Store the artifact and write a `PreferenceModelRegistry` candidate row.
5. Batch-generate predictions into `MoviePreferencePredictions`.
6. Compare candidate predictions against the primary model.
7. Promote a candidate to primary only through an explicit operation.

Web/API and pipeline paths do not train models. If a prediction is missing or
stale, they report that state instead of doing online training.

### Promotion Gates

Candidate promotion requires all of the following:

- minimum explicit rating count;
- time-split validation;
- ranking quality, such as NDCG@20 or Precision@20;
- calibration checks by score bucket;
- regression protection for known high-score and low-score movies;
- top disagreement review between candidate and primary;
- explicit human confirmation through CLI/API.

Training jobs must not automatically switch the primary model.

## Consequences

### Positive

- Preference modeling stays focused on user taste instead of absorbing torrent
  quality and dedup decisions.
- Predictions are explainable, cacheable, auditable, and versioned.
- Web/API can gain useful scoring before the pipeline enforces any behavior.
- Candidate models can be compared safely before promotion.
- Offline training avoids adding latency or instability to request and ingestion
  hot paths.

### Negative

- Phase 1 needs enough explicit ratings before the trainable model becomes more
  useful than rules.
- Batch prediction introduces stale/missing prediction states that consumers must
  handle.
- The first model will not learn from every implicit pipeline behavior.
- Artifact storage and registry management add operational surface area.

### Risks

- **Sparse labels overfit to a few favorite actors** - Mitigation: keep the
  trainable gate at 200+ ratings, use regularization, and report confidence.
- **Implicit behavior pollutes preference labels** - Mitigation: keep implicit
  behavior out of the core Phase 1 label path.
- **Model upgrades silently change pipeline behavior** - Mitigation: production
  reads one primary model, promotion is explicit, and pipeline starts in shadow
  or assist mode.
- **Explanations become misleading** - Mitigation: persist feature schema
  versions and structured feature-group contributions with each prediction.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | Future IMP | D1 prediction and model registry schema, versioned rule baseline, batch prediction, Web/API read path | Trainable model and pipeline enforcement |
| Phase 2 | Future IMP | Offline training CLI/workflow, candidate model artifacts, metrics, shadow comparison | Automatic promotion |
| Phase 3 | Future IMP | Pipeline shadow/assist consumption and promotion workflow | Default enforce mode |

## References

- [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) - data foundation for metadata, ratings, and content preferences.
- [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md) - torrent quality evidence and `quality_score` ownership.
- `docs/design/ADR-022-User-Preference-Foundation/IMP-ADR022-01-db-schema.md` - D1-first schema pattern for preference data.
- `docs/design/ADR-022-User-Preference-Foundation/IMP-ADR022-03-preference-repo.md` - repository and API shape for rating/preference writes.

## Status Log

- 2026-05-27: Proposed as ADR-025.
