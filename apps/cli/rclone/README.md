# rclone

Rclone-backed cloud-storage tooling — the canonical manager plus the JAV-Sync directory cleanup / rename / NFO-rewrite suite.

## Files

| File | Purpose |
|---|---|
| `manager.py` | Canonical rclone manager CLI — drives the dedup / sync flow. Aliases `javdb.integrations.rclone.manager` so tests can patch module-level attributes (e.g. `RCLONE_FOLDER_PATH`). |
| `cleanup_empty_dirs.py` | Remove empty subdirectories under year / 未知 folders on an rclone remote via `rclone rmdirs --leave-root`. |
| `flatten_by_size.py` | Bucket files by size: large files (≥ `--min-mib`, default 200 MiB) get hoisted to the root of their group, smaller ones are deleted. Handles name collisions by prefixing. |
| `group_jav.py` | Reorganise JAV-Sync layout by inserting a per-番号 directory at level 3 — turns `<root>/<年份>/<演员>/<番号 [打码-字幕]>` into `<root>/<年份>/<演员>/<番号>/<打码-字幕>`. |
| `rename_jav.py` | Rename JAV-Sync directories: `[…]` → `(…)`, drop `有码`, rewrite `无码流出` → `流出`, drop `无字`, collapse empty bracket pairs. |
| `update_nfo_titles.py` | Rewrite `<movie>/<title>` inside JAV-Sync `.nfo` files to the canonical `[code actor] title (suffix)` format; relocates orphan-NFO directories whose contents exceed 100 MiB. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.rclone_manager --execute` (canonical: `apps.cli.rclone.manager`).
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.rclone_manager --execute`.
- **`WeeklyDedup.yml`** — `apps.cli.rclone_manager` (weekly dedup cron).
- The five cleanup tools (`cleanup_empty_dirs`, `flatten_by_size`, `group_jav`, `rename_jav`, `update_nfo_titles`) are operator-run on demand.

## Related

- [ADR-007 — Monorepo restructure](../../../docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
