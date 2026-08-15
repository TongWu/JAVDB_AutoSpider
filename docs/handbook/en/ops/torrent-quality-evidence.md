# Torrent Quality Evidence (ADR-024 Phase 1)

Phase 1 is a shadow-only, non-decisioning evidence layer. It inspects file
lists for production-selected, recently added torrents whose qBittorrent file
metadata is available, computes an explainable quality score, and stores
structured evidence. It never changes which torrent the production pipeline
downloads.

## What It Collects

- `TorrentQualityEvidence` stores torrent-level facts keyed by
  `(info_hash, probe_schema_version, target_role)`: total size, main-video size
  and ratio, video/subtitle/non-video file counts, junk size and ratio,
  suspicious file count, and reason codes.
- `TorrentQualityEvaluation` stores movie-context shadow scoring keyed by
  `(info_hash, movie_href, scoring_version)`: score, decision, inferred
  category, subtitle evidence, category consistency, and reason codes.

In GitHub Actions / D1 mode, both tables live in the canonical `javdb-reports`
D1 database. Local runs write according to `STORAGE_BACKEND`, normally the
local SQLite mirror unless configured otherwise; SQLite mirrors are debugging
copies only.

## Enabling It

The collector is disabled by default. Set the GitHub Variable
`TORRENT_QUALITY_EVIDENCE_ENABLED=true`, or set
`TORRENT_QUALITY_EVIDENCE_ENABLED = True` in `config.py` for a local run.

| Key | Default | Meaning |
| --- | --- | --- |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `False` | Master on/off switch for evidence collection. |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | Phase 1 always behaves as shadow mode; `assist` and `enforce` are reserved. |
| `TORRENT_QUALITY_CATEGORIES` | `''` | Optional JSON array of qBittorrent categories to scan. |

## When It Runs

`QBFileFilter.yml` runs the collector immediately after the qBittorrent file
filter step when `TORRENT_QUALITY_EVIDENCE_ENABLED=true`. The collector uses the
same restored encrypted config and the same production qBittorrent endpoint.
Manual workflow dispatches skip the collector when `dry_run=true`, because
evidence rows are persistent database writes.

The workflow resolves evidence categories in this order: manual dispatch
`categories` input, then `TORRENT_QUALITY_CATEGORIES`, then the workflow default
`["Ad Hoc", "Daily Ingestion", "顶级"]`.

You can also run it manually:

```bash
python3 -m apps.cli.qb.quality_evidence --days 2 --categories '["Daily Ingestion"]'

# Run even when TORRENT_QUALITY_EVIDENCE_ENABLED is false.
python3 -m apps.cli.qb.quality_evidence --force --categories '["Daily Ingestion"]'
```

If the collector is disabled and `--force` is not provided, the CLI exits with
status `0` without reading category config or touching qBittorrent. If no
category JSON array is configured or passed for a direct run, collection skips
instead of scanning every qBittorrent category.

## Reading Results

The collector prints a summary line with `scanned`, `evidence_written`,
`evaluations_written`, `probe_unavailable`, and `skipped` counts. Persisted
rows are available in the `TorrentQualityEvidence` and
`TorrentQualityEvaluation` tables in the active reports database: canonical
`javdb-reports` in GitHub Actions / D1 mode, or the database selected by
`STORAGE_BACKEND` for local runs.

The FastAPI read surface exposes the same stored rows:

- `GET /api/quality/evaluations?limit=50` — recent shadow evaluations.
- `GET /api/quality/evaluations?movie_href=/v/...` — evaluations for one movie.
- `GET /api/quality/evidence/{info_hash}` — objective file-list evidence for a
  production-selected torrent whose qBittorrent metadata was captured.

Every `/api/quality/*` endpoint is authenticated. All of the above are read-only;
the one write on this surface is admin-only:

- `POST /api/quality/review-labels` — records an operator review label
  (`accept` / `reject` / `skip`) against an evaluation. The labels are shared,
  tunable state — the dataset ADR-024 Phase 3 tunes thresholds against — not
  per-user data, so a readonly token cannot write them. Request body and
  response shape are in [api-reference.md](../developer/api-reference.md).

## Reason Codes

Scores are explainable. Common reason codes include:

- `main_video_detected` / `main_video_missing`
- `main_video_ratio_low`
- `junk_ratio_high`
- `subtitle_file_present` / `subtitle_file_missing`
- `category_mismatch`
- `abnormal_file_count`
- `probe_unavailable`

## Phase 1 Limits

- File-list metadata only; no frame capture, OCR, watermark detection, or visual
  quality inspection.
- Production-selected torrents only; no Top-K runner-up probing.
- No remote `quality_probe` endpoint yet; only the `production_download` role.
