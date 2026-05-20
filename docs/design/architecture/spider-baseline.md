# Spider Current Layout And Legacy Mapping

## Current Snapshot

- Public package entry remains `python3 scripts/spider` via `scripts/spider/__main__.py`
- App entrypoints live under `scripts/spider/app/`
  - `main.py`: top-level spider orchestration
  - `cli.py`: argument parsing
- Runtime state lives under `scripts/spider/runtime/`
  - `config.py`: config/constants bootstrap
  - `state.py`: mutable runtime state
  - `sleep.py`: adaptive sleep and throttling
  - `report.py`: summary reporting
- Fetch execution lives under `scripts/spider/fetch/`
  - `index.py`: index-page fetching
  - `fallback.py`: direct/proxy/CF/login fallback flow
  - `session.py`: login/session helpers
  - `login_coordinator.py`: parallel login routing
  - `fetch_engine.py`: proxy-worker execution engine
- Detail orchestration lives under `scripts/spider/detail/`
  - `runner.py`: shared detail-stage filtering and persistence
  - `parallel_mode.py`: multi-proxy detail mode
  - `sequential_mode.py`: sequential detail mode
- Spider domain services live under `scripts/spider/services/`
  - `dedup.py`: rclone inventory and dedup decisions
- Compatibility exports live under `scripts/spider/compat/`
  - `csv_builder.py`: CSV builder facade

## Legacy Flat Path To Current Layered Path

| Legacy path | Current path |
| --- | --- |
| `scripts/spider/main.py` | `scripts/spider/app/main.py` |
| `scripts/spider/cli.py` | `scripts/spider/app/cli.py` |
| `scripts/spider/config_loader.py` | `scripts/spider/runtime/config.py` |
| `scripts/spider/state.py` | `scripts/spider/runtime/state.py` |
| `scripts/spider/sleep_manager.py` | `scripts/spider/runtime/sleep.py` |
| `scripts/spider/report.py` | `scripts/spider/runtime/report.py` |
| `scripts/spider/index_fetcher.py` | `scripts/spider/fetch/index.py` |
| `scripts/spider/fallback.py` | `scripts/spider/fetch/fallback.py` |
| `scripts/spider/session.py` | `scripts/spider/fetch/session.py` |
| `scripts/spider/parallel_login.py` | `scripts/spider/fetch/login_coordinator.py` |
| `scripts/spider/engine.py` | `scripts/spider/fetch/fetch_engine.py` |
| `scripts/spider/detail_runner.py` | `scripts/spider/detail/runner.py` |
| `scripts/spider/parallel.py` | `scripts/spider/detail/parallel_mode.py` |
| `scripts/spider/sequential.py` | `scripts/spider/detail/sequential_mode.py` |
| `scripts/spider/dedup_checker.py` | `scripts/spider/services/dedup.py` |
| `scripts/spider/csv_builder.py` | `scripts/spider/compat/csv_builder.py` |

## Usage Rule

- New spider implementation work should target the layered paths above.
- Public invocation stays `python3 scripts/spider`.
- `scripts/_spider_legacy.py` remains a historical compatibility reference, not the primary implementation surface.
