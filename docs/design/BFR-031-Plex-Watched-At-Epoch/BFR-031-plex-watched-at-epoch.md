# BFR-031: Plex `lastViewedAt` epoch stored unnormalized, breaking consumption trends

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/integrations/media_servers/plex/adapter.py`, `javdb/migrations/d1/2026_08_09_normalize_plex_watched_at_epoch.sql`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [IMP-ADR033-03](../_archive/ADR-033-Media-Closed-Loop/IMP-ADR033-03-consumption-signal.md), issue #271

---

## Symptom

No incident was observed. Found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153), filed as issue #271 and confirmed against the code.

Plex watch events were missing from the consumption trend, or appeared under a nonsense day key. Emby watch events on the same deployment were fine.

## Root Cause

The Plex adapter passed Plex's native value straight through:

```python
watched_at=str(raw["lastViewedAt"]) if raw.get("lastViewedAt") else None,
```

Plex returns `lastViewedAt` as a **numeric Unix epoch** (seconds), so this stored `'1712345678'`. The Emby adapter's `ud.get("LastPlayedDate")` is already an ISO string, so the two adapters wrote two different shapes into the same `ConsumptionSignal.watched_at` column.

Everything downstream assumes ISO. The trend builder is explicit about it:

```sql
SELECT substr(watched_at, 1, 10) AS d, ...
WHERE watched_at IS NOT NULL AND watched_at >= ?
```

with the cutoff bound as `YYYY-MM-DD`. Against `'1712345678'` that yields two distinct failures, depending on the digits:

- `substr(...)` returns `'1712345678'`, which is not a date, so any row that *does* pass the filter is grouped under an invalid key.
- The filter itself is a **string** comparison. `'1712345678' >= '2026-05-01'` is false, so present-day epochs are silently dropped — while a future epoch beginning with a digit above `'2'` would compare greater than every real date and pollute *every* window.

The design flaw is that `MediaItem.watched_at` is typed `Optional[str]` and documented as ISO, but nothing enforced it. The contract lived in a docstring, and the adapter boundary — the exact place where two vendor formats have to converge — is where it was skipped. `IMP-ADR033-03` even pins the intended shape (`utc_now_iso()`, `YYYY-MM-DDTHH:MM:SS.ffffffZ`) and the `substr` grouping rule; the Plex path just never implemented its half.

## Fix

Normalize at the adapter boundary via `_watched_at_iso`, so both adapters emit one shape:

- numeric (or all-digit string) → `datetime.fromtimestamp(epoch, tz=timezone.utc)` rendered with a trailing `Z`;
- non-numeric → passed through unchanged, so a server that already sends ISO keeps working;
- falsy (absent, empty, epoch `0`) → `None`, preserving the previous guard's meaning of "never viewed" rather than claiming 1970;
- out-of-range epoch → logged and dropped rather than raising mid-scan.

Rows already stored are repaired by `2026_08_09_normalize_plex_watched_at_epoch.sql`. It matches on **shape** (`watched_at NOT GLOB '*[^0-9]*'`) rather than on `source_type`, which makes it idempotent by construction: a real timestamp always contains `-`, so a normalized row can never match on a re-run.

## Side Effects

None observed. The backfill ran against production D1 on 2026-08-10 and matched **zero rows** (see Follow-Up), so no stored value changed and no chart moved. Had there been affected rows they would have shifted to their true day — a chart screenshotted before the migration would not have matched one taken after.

Rows whose `watched_at` was already ISO are untouched, as are all Emby rows.

## Follow-Up

- [x] **Backfill applied to D1 on 2026-08-10** via `wrangler d1 execute javdb-operations --remote`: 1 query, 1 row read, **0 rows written**.

  This answers the question the issue left open ("worth checking whether existing rows need a backfill, or whether the bad values are sparse enough to leave"): **no bad rows were ever stored.** That is consistent with the issue reporter's own note that the Plex path had not been run against a live server — the defect was real in code but had not yet produced data. The migration is retained rather than deleted: it is idempotent, it documents the repair, and it protects any deployment that *did* run the Plex path before this fix.
- [ ] Re-align the local SQLite mirror: `python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`.
