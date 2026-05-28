# BFR-007: Pipeline reports artifact packages SQLite DBs and stale Dedup CSVs in D1 mode

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `.github/workflows/DailyIngestion.yml`
**Related**: [Issue #111](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/111), ADR-017 (D1 canonical source)

---

## Symptom

DailyIngestion run 26512511043 (`STORAGE_BACKEND=d1`) packaged the following into `reports.tar.gz.enc` (14.1 MB):

- `reports/history.db`, `reports/reports.db`, `reports/operations.db` — SQLite mirrors that are read-only in D1 mode and can be rebuilt via `sync_d1_to_sqlite`
- `reports/parsed_movies_history.csv` — legacy CSV history, not authoritative in D1 mode
- 13 historical Dedup CSVs from March-May 2026, unrelated to the current run

At 7-day retention with daily runs, this wastes ~100 MB of GitHub Actions artifact storage.

## Root Cause

The "Encrypt reports" step in `DailyIngestion.yml:750-776` unconditionally includes:

1. **Line 750**: Hardcoded list of `.db` and `.csv` files without checking `$STORAGE_BACKEND`
2. **Line 772-776**: `find "$REPORTS_DIR/Dedup" -name "*.csv"` collects ALL historical Dedup CSVs instead of just the current run's output

## Fix

1. Skip `.db` files and `parsed_movies_history.csv` when `STORAGE_BACKEND=d1`
2. Replace the `find` for Dedup CSVs with the specific path from the spider step's output (`$DEDUP_CSV_PATH`)
3. Add a workflow regression test that guards the D1 condition and current-run Dedup path

## Side Effects

Artifact size drops from ~14 MB to ~1-2 MB in D1 mode. Non-D1 runs are unaffected.

## Follow-Up

- [x] Condition `.db` and legacy CSV inclusion on `$STORAGE_BACKEND`
- [x] Use spider output for Dedup CSV path instead of `find`
- [x] Add workflow regression test for the artifact file list
