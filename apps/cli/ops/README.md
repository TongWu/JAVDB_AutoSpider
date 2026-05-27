# ops

Operator tooling — workflow bootstrap, debugging, health checks, profiling, and miscellaneous one-shots.

## Files

| File | Purpose |
|---|---|
| `config_generator.py` | Generate `config.py` from secrets / environment at the start of every workflow job. Aliases `javdb.infra.config_generator`. Invoked as `python3 -m apps.cli.config_generator --github-actions`. |
| `diagnose_run.py` | Read-only ADR-026 operations diagnosis for failed workflow runs, sessions, D1 drift, recovery outbox state, and qB side-effect evidence. |
| `fetch_page.py` | Standalone JAVDB page fetcher (debugging / fixture capture). Aliases `javdb.infra.fetch_page`. |
| `health_check.py` | Pre-flight check for required services (qBittorrent, proxies, JAVDB reachability). Aliases `javdb.infra.health_check`. Workflows call this before the spider step. |
| `profile_hot_paths.py` | Micro-benchmark spider hot paths to locate the next Rust acceleration target. Offline fixtures only; outputs `pstats` dumps under `reports/profiling/`. |
| `dump_openapi.py` | Dump the FastAPI app's OpenAPI schema to `docs/api/openapi.json`. |

## Invoked by

- **`DailyIngestion.yml` / `AdHocIngestion.yml` / `AuditArchive.yml` / `StaleSessionCleanup.yml` / `RollbackD1.yml`** — every workflow runs `python3 -m apps.cli.config_generator --github-actions` as its bootstrap step.
- **`DailyIngestion.yml` / `AdHocIngestion.yml`** — `python3 -m apps.cli.health_check` before the spider step.
- **`DailyIngestion.yml` / `AdHocIngestion.yml`** — on failed or cancelled runs, `python3 -m apps.cli.ops.diagnose_run` creates a persisted read-only incident record for email/API review.
- `profile_hot_paths`, `dump_openapi`, `fetch_page` are operator-run on demand.

## Related

- [ADR-005 — db.py retirement and repo pattern](../../../docs/design/_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- [ADR-006 — Pending mode default rollout](../../../docs/design/_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md)
- [ADR-007 — Monorepo restructure](../../../docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
