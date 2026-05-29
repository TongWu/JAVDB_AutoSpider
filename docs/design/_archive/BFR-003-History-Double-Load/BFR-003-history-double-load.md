# BFR-003: Spider loads parsed_movies_history twice on startup (~12s wasted)

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `javdb/spider/app/run_service.py`, `javdb/storage/history_manager.py`
**Related**: [Issue #107](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/107), ADR-017 (D1 canonical source)

---

## Symptom

DailyIngestion run 26512511043 logged two identical history loads 6 seconds apart:

```
21:00:42 History  Loaded 40383 previously parsed movies from history
21:00:48 History  Loaded 40383 previously parsed movies from history
```

Total wasted time: ~12 seconds of D1 queries + parsing.

## Root Cause

`run_service.py:366-367` calls `load_parsed_movies_history()` twice in sequence:

```python
parsed_movies_history_phase1 = load_parsed_movies_history(history_file, phase=1)
parsed_movies_history_phase2 = load_parsed_movies_history(history_file, phase=None)
```

In SQLite/D1 mode (`use_sqlite()` returns True), each call triggers `HistoryRepo().load_history(phase=...)` which executes a full `SELECT * FROM MovieHistory`, then filters in Python. Phase 1 data is always a subset of the phase=None (all phases) data, so the first query is redundant — its result can be derived from the second.

## Fix

Load history once with `phase=None`, then derive the phase-1 view locally. DB-backed history no longer stores phase, so the derived phase-1 view keeps all rows (matching the previous ignored-`phase` DB behavior); CSV fallback rows still carry `phase`, so phase-2-only rows are excluded to preserve legacy CSV semantics. This halves D1 reads and eliminates ~6s of startup time.

## Side Effects

None — same data, fewer queries.

## Follow-Up

- [x] Merge two `load_parsed_movies_history` calls into one load + derived phase-1 view
- [x] Add a regression test for CSV phase-2 exclusion during local derivation
