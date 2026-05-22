# Python Tree (post-2026-05 restructure)

This is the current canonical layout, established by [ADR-007](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md).

## Top-level

```
JAVDB_AutoSpider_CICD/
├── apps/
│   ├── api/                  # FastAPI service
│   ├── cli/                  # All Python CLI entries
│   ├── web/                  # FE (being replaced; see javdb-autospider-web)
│   └── desktop/              # Electron shell (MVP)
├── javdb/                    # Python namespace (PEP 420; no __init__.py)
│   ├── spider/
│   ├── pipeline/
│   ├── storage/
│   ├── proxy/
│   ├── integrations/
│   ├── infra/
│   ├── migrations/
│   └── rust_core/            # Rust crate; installed as javdb.rust_core
├── docker/, docs/, tests/, scripts/
└── config.py, pytest.ini, requirements.txt, README*, CLAUDE.md, CONTEXT.md
```

## Per-package quick reference

(Each package has its own README; this is a one-line summary.)

- `javdb/spider/` — scraping runtime + parser/contracts/url/filename/magnet + auth/login
- `javdb/pipeline/` — orchestration (was `ingestion`) + pipeline service
- `javdb/storage/` — DB layer + sessions + rollback + d1 + dual + history_manager
- `javdb/proxy/` — pool + ban_manager + policy + recommend/ + coordinator/ (Worker DO clients)
- `javdb/integrations/` — qb/, pikpak/, rclone/, notify/
- `javdb/infra/` — cross-cutting: config, logging, paths, csv_writer, git, request, masking, fetch_page, health_check
- `javdb/migrations/` — SQL + Python migrate tools
- `javdb/rust_core/` — Rust crate source (PyO3 + maturin)

## CLI quick reference (apps/cli/)

Core: `spider`, `pipeline`, `login`.

Subdirectories: `db/`, `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/`. Invoke via `python -m apps.cli.<subdir>.<name>`.

## Replaces

- `docs/design/architecture/python-core-mapping.md` — Old partial-reorg mapping (SUPERSEDED)
- `docs/design/architecture/spider-module-reorg.md` — Old spider-only reorg status (SUPERSEDED)

## See also

- ADR-007 — decision and three-phase rollout
- ADR-007 deletion manifest — what was removed in Phase 3
