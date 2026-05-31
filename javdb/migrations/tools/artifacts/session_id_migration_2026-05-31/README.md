# Legacy session-id → TEXT migration — 2026-05-31

Audit + rollback artifact for the run of
[`migrate_session_ids_to_text.py`](../../migrate_session_ids_to_text.py) that
normalised 362 legacy `ReportSessions` ids (integer + snowflake variants) to the
canonical `YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS` format across the three D1
databases.

## `map.json`

362 entries, one per migrated session:

```json
{ "old_id": "1", "date_time_created": "2026-03-10 19:43:44",
  "new_id": "20260310T194344.000000Z-0000-0000", "synthetic": true }
```

`synthetic: true` flags that the microsecond / TTTT fields are placeholders
(`000000` / `0000`) and SSSS is a collision counter — these ids do **not** encode
a real per-session time. 251 of the 362 sessions were a same-second bulk import
(`2026-03-10 19:43:44`) and differ only by SSSS.

## Verification (passed)

- `ReportSessions`: 0 legacy-format ids remaining (394 rows all canonical).
- `--verify`: 0 legacy refs anywhere, every new id present as PK, per-table
  reference counts + total row counts conserved, **no new orphans** (within the
  migration's scope; pre-existing orphans are called out below).
- `PRAGMA foreign_key_check` (reports D1): no new violations — the 14 that remain
  are pre-existing orphans (sessions `332`, `1820929777505280`) from
  rolled-back sessions, out of this migration's scope.

## Rollback

To reverse, apply each mapping the other way (`new_id` → `old_id`) across the
same columns the tool touches. The REPORTS-db FK group (`ReportSessions` +
`ReportMovies` / `SpiderStats` / `UploaderStats` / `PikpakStats`) must be
reversed in one transaction under `PRAGMA defer_foreign_keys=on`, exactly as the
forward migration does — see the tool's `apply_mapping` for the pattern.
