# Spider Baseline And Legacy Mapping

## Baseline Snapshot

- Primary entry: `scripts/spider/main.py`
- Index acquisition: `scripts/spider/index_fetcher.py`
- Detail processing:
  - Sequential: `scripts/spider/sequential.py`
  - Parallel proxy workers: `scripts/spider/parallel.py`
- Retry/fallback and auth handling: `scripts/spider/fallback.py`, `scripts/spider/session.py`
- CSV row build and filtering: `scripts/spider/csv_builder.py`
- Dedup and rclone inventory checks: `scripts/spider/dedup_checker.py`
- Shared state/config: `scripts/spider/state.py`, `scripts/spider/config_loader.py`

## Legacy To New Mapping

| Legacy capability (`scripts/_spider_legacy.py`) | New location |
| --- | --- |
| CLI and runtime bootstrap | `scripts/spider/cli.py`, `scripts/spider/main.py` |
| Proxy pool setup and request handler init | `scripts/spider/state.py` |
| Index page fetch and validation | `scripts/spider/index_fetcher.py`, `scripts/spider/fallback.py` |
| Detail page parse loop (sequential) | `scripts/spider/sequential.py` |
| Multi-proxy parallel queue workers | `scripts/spider/parallel.py` |
| Login refresh and login-page detection | `scripts/spider/session.py` |
| CSV row creation and history-aware filtering | `scripts/spider/csv_builder.py` |
| Rclone skip + dedup decision | `scripts/spider/dedup_checker.py` |
| End-of-run summary report | `scripts/spider/report.py` |

## Migration Rule

- New feature work must target `scripts/spider/` only.
- `scripts/_spider_legacy.py` stays for compatibility and rollback reference.
- If behavior diverges, fix in `scripts/spider/` first, then backport only when strictly required.
