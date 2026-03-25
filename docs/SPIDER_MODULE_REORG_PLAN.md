# Python Core Reorganization Status

## Scope

This reorganization intentionally covers the Python core runtime only:

- `scripts/spider`
- core `utils` modules used by spider, API, migration tools, and pipeline
- internal imports in `api/server.py`, `pipeline.py`, `migration/tools/*`, and tests
- documentation describing the Python module layout

It intentionally does **not** restructure `web/`, `electron/`, `rust_core/`, workflow filenames, or Docker directory layout in this batch.

## Naming Rules

- `engine`: execution runtime only
- `runner`: business orchestration only
- `planner`: pure decision-making only
- `runtime`: config, mutable state, sleep, reporting
- `services`: spider domain services
- `compat`: explicit compatibility facade only
- `infra` / `domain` / `bridges`: shared utilities grouped by responsibility

## Implemented Spider Layout

```text
scripts/spider/
├── __main__.py
├── app/
│   ├── cli.py
│   └── main.py
├── runtime/
│   ├── config.py
│   ├── state.py
│   ├── sleep.py
│   └── report.py
├── fetch/
│   ├── index.py
│   ├── fallback.py
│   ├── session.py
│   ├── login_coordinator.py
│   └── fetch_engine.py
├── detail/
│   ├── runner.py
│   ├── parallel_mode.py
│   └── sequential_mode.py
├── services/
│   └── dedup.py
└── compat/
    └── csv_builder.py
```

## Implemented Shared Utility Layout

```text
utils/
├── infra/
│   ├── config_helper.py
│   ├── logging_config.py
│   ├── request_handler.py
│   ├── proxy_pool.py
│   ├── db.py
│   ├── db_layer/
│   ├── csv_writer.py
│   ├── git_helper.py
│   └── path_helper.py
├── domain/
│   ├── contracts.py
│   ├── url_helper.py
│   ├── magnet_extractor.py
│   ├── filename_helper.py
│   └── masking.py
├── bridges/
│   └── rust_adapters/
├── history_manager.py
├── parser.py
├── proxy_ban_manager.py
├── rclone_helper.py
├── spider_gateway.py
└── sqlite_datetime.py
```

## Stable Public Contracts

These user-visible entrypoints stay unchanged:

- `python3 scripts/spider`
- `python3 pipeline.py`
- `api/server.py`
- workflow filenames under `.github/workflows/`
- `docker/Dockerfile`
- `docker/docker-entrypoint.sh`

## Internal Import Baseline

- Spider implementation imports now target layered paths under `scripts.spider.app/runtime/fetch/detail/services/compat`
- Shared utility imports now target `utils.infra.*`, `utils.domain.*`, and `utils.bridges.*`
- `api/server.py`, `pipeline.py`, `migration/tools/*`, and tests were updated to the new canonical Python import paths

## Validation

The reorganization was validated with:

- full `pytest`
- `python3 -m py_compile` over `scripts/`, `utils/`, `api/`, `migration/`, `tests/`, and `pipeline.py`
- `python3 scripts/spider --help`
- `python3 pipeline.py --help`
- import smoke for `api/server.py`

## Follow-Up Work

- Generalize `fetch_engine.py` into a backend interface if sequential mode should eventually share the same execution runtime
- Continue reducing docs/comments that still describe the older flat spider layout where helpful
- Keep `api/parsers` and `scripts/ingestion` as stable sources of truth in future reorg steps
