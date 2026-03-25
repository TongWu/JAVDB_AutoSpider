# Python Core Old-To-New Mapping

## Stable External Contracts

These paths and commands remain unchanged:

| Surface | Status |
| --- | --- |
| `python3 scripts/spider` | unchanged |
| `python3 pipeline.py` | unchanged |
| `api/server.py` | unchanged |
| `.github/workflows/*.yml` filenames | unchanged |
| `docker/Dockerfile` | unchanged |
| `docker/docker-entrypoint.sh` | unchanged |

## Spider Module Mapping

| Old path | New path |
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

## Shared Utility Mapping

| Old path | New path |
| --- | --- |
| `utils/config_helper.py` | `utils/infra/config_helper.py` |
| `utils/logging_config.py` | `utils/infra/logging_config.py` |
| `utils/request_handler.py` | `utils/infra/request_handler.py` |
| `utils/proxy_pool.py` | `utils/infra/proxy_pool.py` |
| `utils/db.py` | `utils/infra/db.py` |
| `utils/db_layer/*` | `utils/infra/db_layer/*` |
| `utils/csv_writer.py` | `utils/infra/csv_writer.py` |
| `utils/git_helper.py` | `utils/infra/git_helper.py` |
| `utils/path_helper.py` | `utils/infra/path_helper.py` |
| `utils/contracts.py` | `utils/domain/contracts.py` |
| `utils/url_helper.py` | `utils/domain/url_helper.py` |
| `utils/magnet_extractor.py` | `utils/domain/magnet_extractor.py` |
| `utils/filename_helper.py` | `utils/domain/filename_helper.py` |
| `utils/masking.py` | `utils/domain/masking.py` |
| `utils/rust_adapters/*` | `utils/bridges/rust_adapters/*` |

## Intentionally Stable Top-Level Utility Modules

These modules stay in place for now and were not moved in this batch:

- `utils/history_manager.py`
- `utils/parser.py`
- `utils/proxy_ban_manager.py`
- `utils/rclone_helper.py`
- `utils/spider_gateway.py`
- `utils/sqlite_datetime.py`
- `utils/login/*`
- `utils/config_generator.py`

## Affected Internal Callers

The following areas were updated to use the new canonical import paths:

- `scripts/*`
- `api/server.py`
- `pipeline.py`
- `migration/tools/*`
- `tests/*`

## Validation Checklist

- Full `pytest`
- `python3 -m py_compile` for Python packages and tests
- `python3 scripts/spider --help`
- `python3 pipeline.py --help`
- `api/server.py` import smoke
