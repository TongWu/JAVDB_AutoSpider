// AUTO-GENERATED from javdb/storage/contract — DO NOT EDIT.
// Source of truth: ADR-055. Regenerate: python3 -m apps.cli.ops.dump_sql_contract
// version: bed8e18bd043de4b
/* eslint-disable */

export const WATCH_INTENT_UPSERT_SQL =
  `INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(video_code) DO UPDATE SET href = excluded.href, status = excluded.status, notes = COALESCE(excluded.notes, notes), status_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export function prepareWatchIntentUpsert(
  db: D1Database,
  p: { videoCode: string; href: string; status: string; notes: string | null },
): D1PreparedStatement {
  return db.prepare(WATCH_INTENT_UPSERT_SQL).bind(p.videoCode, p.href, p.status, p.notes);
}
