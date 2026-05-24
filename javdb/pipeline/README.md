# pipeline

Ingestion pipeline orchestration: transforms parsed spider output into CSV rows, qBittorrent plans, and rollback-aware history writes.

## Files

| File | Purpose |
|---|---|
| `models.py` | Shared ingestion data models (`ParsedMovie`, etc.) used across planner/adapter/policy layers. |
| `policies.py` | Pure ingestion policies (category families, torrent-type detection, alignment ranking) shared by spider and migration tools. |
| `adapters.py` | Adapters that convert ingestion decisions into external row formats (CSV rows, qB alignment rows). |
| `planner.py` | High-level ingestion planners built on shared policies and adapters. |
| `engine.py` | Compatibility re-export surface that bundles models/policies/adapters/planners for older callers and tests. |
| `service.py` | Orchestration service that chains spider → uploader → pikpak → notification (the long-running pipeline). |

## Run Output Contract

Pipeline logs remain a real-time human stream for operators, GitHub Actions logs,
and frontend task views. Machine-readable Pipeline and Spider run data is written
to versioned result JSON sidecars.

`SPIDER_*` stdout lines are compatibility output for existing workflows. They are
not the internal Pipeline protocol, and new Pipeline internals should read
business data from result JSON instead of parsing those stdout markers.

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.pipeline`, `javdb.spider.fetch.index`, `javdb.spider.detail.runner`, `javdb.spider.compat.csv_builder`.
- Downstream: `javdb.spider.contracts`, `javdb.spider.services.dedup`, `javdb.spider.magnet_extractor`, `javdb.infra.logging`, `javdb.storage`.
