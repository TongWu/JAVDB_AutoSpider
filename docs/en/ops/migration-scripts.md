# Migration Scripts

Tools for upgrading database schemas, cleaning up history data, and converting between formats.

## Primary Migration Entry Point

```bash
python3 -m javdb.migrations.migrate_to_current --help
```

`migrate_to_current.py` is the main entry point for SQLite schema upgrades. It supports optional datetime normalization and actor backfill. Run with `--help` to see all available options.

## One-Off and Legacy Helpers

The `javdb/migrations/tools/` directory contains one-off migration scripts for specific upgrade tasks.

### cleanup_history_priorities.py

Removes duplicate entries from the history file.

- Ensures data integrity
- Safe to run multiple times (idempotent)

```bash
python3 javdb/migrations/tools/cleanup_history_priorities.py
```

### update_history_format.py

Migrates old history format to the new format.

- Converts `parsed_date` to `create_date` / `update_date`
- Automatic backward compatibility

```bash
python3 javdb/migrations/tools/update_history_format.py
```

### rename_columns_add_last_visited.py

Renames date columns and adds the `last_visited_datetime` field.

- Required when upgrading to support the new history format

```bash
python3 javdb/migrations/tools/rename_columns_add_last_visited.py
```

### migrate_reports_to_dated_dirs.py

Migrates flat report files into `YYYY/MM/` dated subdirectories.

- Required when upgrading to the new reports directory structure
- Supports `--dry-run` to preview changes without moving files

```bash
# Preview changes first
python3 javdb/migrations/tools/migrate_reports_to_dated_dirs.py --dry-run

# Apply
python3 javdb/migrations/tools/migrate_reports_to_dated_dirs.py
```

### reclassify_c_hacked_torrents.py

Reclassifies torrents with specific naming patterns.

- Updates torrent type classification
- Useful after classification rule changes

```bash
python3 javdb/migrations/tools/reclassify_c_hacked_torrents.py
```

## When to Run Migration Scripts

Run migration scripts when:

- Upgrading from older versions of the project
- The history file shows duplicate entries
- Format changes have been introduced in a new release
- Data cleanup is needed after a bug fix or classification change

## Important Notes

- **Always back up first**: Before running any migration script, back up your `reports/parsed_movies_history.csv` and SQLite databases (`reports/history.db`, `reports/reports.db`, `reports/operations.db`).
- **Run from repository root**: All scripts expect to be run from the project root directory.
- **Use `--dry-run` when available**: Scripts that support `--dry-run` should be previewed before applying changes.
- **Idempotent where possible**: Most cleanup scripts are safe to run multiple times, but always verify with a dry run first.
