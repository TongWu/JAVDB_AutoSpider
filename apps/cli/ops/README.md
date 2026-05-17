# ops

Operator tooling — workflow bootstrap, debugging, health checks, profiling, and miscellaneous one-shots.

## Files

| File | Purpose |
|---|---|
| `config_generator.py` | Generate `config.py` from secrets / environment at the start of every workflow job. Aliases `javdb.infra.config_generator`. Invoked as `python3 -m apps.cli.config_generator --github-actions`. |
| `fetch_page.py` | Standalone JAVDB page fetcher (debugging / fixture capture). Aliases `javdb.infra.fetch_page`. |
| `health_check.py` | Pre-flight check for required services (qBittorrent, proxies, JAVDB reachability). Aliases `javdb.infra.health_check`. Workflows call this before the spider step. |
| `check_bake_metrics.py` | ADR-005 D10 gate — checks the 30-day ADR-006 bake metrics (no new audit-mode sessions, no orphan audit rows, pause-script trigger count ≤ 1/month). Exit 0 if all three pass. |
| `profile_hot_paths.py` | Micro-benchmark spider hot paths to locate the next Rust acceleration target. Offline fixtures only; outputs `pstats` dumps under `reports/profiling/`. |
| `dump_openapi.py` | Dump the FastAPI app's OpenAPI schema to `docs/api/openapi.json`. |

## Invoked by

- **`DailyIngestion.yml` / `AdHocIngestion.yml` / `AuditArchive.yml` / `StaleSessionCleanup.yml` / `RollbackD1.yml`** — every workflow runs `python3 -m apps.cli.config_generator --github-actions` as its bootstrap step.
- **`DailyIngestion.yml` / `AdHocIngestion.yml`** — `python3 -m apps.cli.health_check` before the spider step.
- `check_bake_metrics`, `profile_hot_paths`, `dump_openapi`, `fetch_page` are operator-run on demand.

## Related

- [ADR-005 — db.py retirement and repo pattern](../../../docs/ai/adr/ADR-005-db-py-retirement-and-repo-pattern.md)
- [ADR-006 — Pending mode default rollout](../../../docs/ai/adr/ADR-006-pending-mode-default-rollout.md)
- [ADR-007 — Monorepo restructure](../../../docs/ai/adr/ADR-007-monorepo-restructure-2026-05.md)
