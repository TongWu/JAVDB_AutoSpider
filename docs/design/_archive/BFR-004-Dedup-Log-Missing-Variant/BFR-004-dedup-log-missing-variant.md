# BFR-004: DEDUP upgrade log appears duplicated for same movie code

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Low
**Affected**: `javdb/spider/services/dedup.py`, `javdb/spider/detail/runner.py`
**Related**: [Issue #108](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/108)

---

## Symptom

DailyIngestion run 26512511043 Phase 1 logged the same "Subtitle upgrade" DEDUP message 2-3 times per movie code (e.g. MIDA-616, JUR-703, SNOS-185) from the same worker within 0.3s. The subsequent rclone purge phase confirmed multiple distinct variants were purged per code (e.g. `MIDA-616/无码破解-无字` and `MIDA-616/有码-无字`).

## Root Cause

This is **expected behavior**, not a bug. `check_dedup_upgrade()` in `dedup.py:314-319` iterates over all `rclone_entries` for a given video code. When multiple GDrive variants exist for the same code (e.g. both `有码-无字` and `无码破解-无字`), each matching entry generates its own `DedupRecord`. The log message does not include the variant/folder path, making identical-looking log lines appear like duplicates.

The DEDUP log in the detail runner prints the reason string without the variant discriminator, so entries like:
```
DEDUP: MIDA-616 - Subtitle upgrade (中字 found, replacing 无字)
DEDUP: MIDA-616 - Subtitle upgrade (中字 found, replacing 无字)
```
are actually two different variants being correctly identified.

## Fix

Include the GDrive folder path or sensor/subtitle category in the DEDUP log line so each entry is visually distinct.

## Side Effects

None — log format change only.

## Follow-Up

- [x] Add variant info (sensor+subtitle category) to DEDUP log output in `detail/runner.py`
- [x] Add a regression test for the DEDUP log variant label helper
