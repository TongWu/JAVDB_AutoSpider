# repos

Repository-pattern wrappers over the raw `javdb.storage.db` modules — provide higher-level, query-shaped APIs for application and API layers.

## Files

| File | Purpose |
|---|---|
| `history_repo.py` | History-related SQLite helpers used by `utils.infra.db`; movie/torrent lookups and aggregations. |
| `operations_repo.py` | Operations DB helpers including the X3 staging-then-swap inventory replacement pattern. |
| `sessions_repo.py` | Cursor-paginated listing and per-session detail queries over `ReportSessions`; exposes Python-friendly field names. |
| `system_state_repo.py` | Generic key-value store against the `system_state` table in `operations.db`. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.api.routers.*`, `apps.cli.rollback`, `apps.cli.migration`, `javdb.pipeline.service`.
- Downstream: `javdb.storage.db.*`, `apps.api.parsers.common`, `javdb.infra.logging`.
