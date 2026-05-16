# sessions

Session commit library: force a session out of `in_progress` or `finalizing` into the terminal `committed` state, used by API and CLI tools.

## Files

| File | Purpose |
|---|---|
| `commit.py` | `commit_session(CommitRequest) -> CommitResult` — single-session commit helper invoked by `POST /api/sessions/{id}/commit` and equivalent CLI flags. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.api.routers.sessions`, `apps.cli.rollback` / commit tooling.
- Downstream: `javdb.storage.db.db_reports`, `javdb.storage.db.db_session`, `javdb.storage.repos.sessions_repo`, `javdb.infra.logging`.
