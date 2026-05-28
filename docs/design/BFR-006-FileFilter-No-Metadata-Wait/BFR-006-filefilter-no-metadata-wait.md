# BFR-006: file_filter skips 55% of new torrents because metadata is not yet available

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: High
**Affected**: `javdb/integrations/qb/file_filter.py`, `.github/workflows/DailyIngestion.yml`
**Related**: [Issue #110](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/110)

---

## Symptom

DailyIngestion run 26512511043: qb.file_filter ran 36 seconds after qb.uploader finished. Of 31 torrents processed, 17 (55%) were skipped with `Metadata not yet available for: <name> (will be processed on next run)`. Small files (ads, HTML, txt) in those 17 torrents downloaded unfiltered for ~2 hours until the cron-based `QBFileFilter.yml` ran.

Additionally, 2 torrents added 24+ hours prior (MIGD-571, MXGS-1403) were still pending metadata, indicating tracker connectivity issues unrelated to this timing bug.

## Root Cause

`file_filter.py:581-586` checks `len(files) == 0` and immediately skips with a log message — no retry or wait logic:

```python
if len(files) == 0:
    logger.info(f"  Metadata not yet available for: {torrent_name} ...")
    stats['pending_metadata'] += 1
    continue
```

In the DailyIngestion pipeline, file_filter runs immediately after uploader in the same job step. qBittorrent typically needs 30s-2min to fetch metadata from trackers after a torrent is added. The 36s gap is insufficient for most torrents.

## Fix

Add a metadata readiness polling loop at the start of `filter_small_files()`: before processing the torrent list, wait up to 90 seconds for the majority of recently-added torrents to have metadata available, polling qBittorrent every 10 seconds. The wait only considers torrents added within the recent metadata window, so older tracker-stuck torrents do not force every run to wait.

## Side Effects

Pipeline runtime increases by up to 90 seconds in the worst case (recent torrents slow to get metadata). In practice, most metadata arrives within 30-60s, so the typical delay is 20-40s with significantly better filter coverage. Older torrents that still have no metadata remain deferred to the normal pending-metadata path.

## Follow-Up

- [x] Add metadata readiness polling in file_filter before processing
- [x] Limit polling to recently-added torrents
- [x] Add unit test for the polling logic
