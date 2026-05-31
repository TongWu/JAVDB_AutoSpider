# ADR-033: Media Closed-Loop — Acquisition Outcome, Ownership Truth & Consumption Signal

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; Phase 1 implemented and locally verified; execution delegated to per-phase IMPs |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions not yet
> captured by any existing ADR.

## Context

The system's cognition **stops at the moment a magnet is added to qBittorrent.**
It optimizes the *acquisition decision* but is blind to what happens afterward —
whether the download completed, where the file actually landed, and whether it
was ever watched. Concretely, against the current code:

- **`MovieHistory` / `TorrentHistory` record only what was scraped and selected**
  (video code, magnet, subtitle/censor/resolution indicators, size, file count).
  There is **no download-outcome state** — no qB hash linkage, no
  `completed` / `failed` / `stalled`, no completion timestamp.
- **The only "ownership truth" is `RcloneInventory`** — a periodic rclone
  snapshot of the GDrive remote (keyed by video code + category, refreshed weekly
  by `WeeklyDedup`). It covers GDrive only and reflects file presence only. The
  dedup checker (`javdb/spider/services/dedup.py`) reads it to decide
  skip/upgrade.
- **qB completion is transient.** `remove_completed_torrents_keep_files` can
  query the `completed` filter, but completion is **never persisted** to the DB.
  The torrent's journey qB → completed → synced to GDrive is not recorded as a
  linked lifecycle.
- **No media-server integration exists** (`jellyfin|emby|plex|kodi|stash` is
  absent from the codebase) and there is **no consumption / watch signal** of any
  kind.

A second structural fact shapes the whole design: **closed-loop data is produced
asynchronously, after the run ends.** The daily pipeline runs once on a GitHub
Actions runner (`ubuntu-latest` or `self-hosted`) and adds torrents at the *end*
of the run, but completion, landing, and watching happen minutes-to-days *later*.
The loop therefore cannot live inside the daily run — it needs a separate,
recurring reconciliation pass.

This blindness has three costs:

1. **Dedup cannot tell "downloading" from "never tried" from "already owned"** —
   it only knows scrape history and a weekly GDrive snapshot.
2. **Failures and stalls are invisible** — a torrent that never completes leaves
   no trace distinguishable from one that succeeded.
3. **The deferred preference model ([ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) /
   [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md))
   lacks its single strongest implicit signal** — actual watch behavior — because
   nothing reads the media servers the operator already runs (Emby + Plex).

This ADR closes the loop in three layers, governed as one umbrella initiative
sequenced into phases (mirroring the [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)
umbrella pattern).

## Decision

Build a **media closed-loop** in three layers — *acquisition outcome →
ownership truth → consumption signal* — backed by new D1-canonical enrichment
tables and an asynchronous reconciliation service. The loop is additive: each new
data source is a new collector behind one service; the orchestration does not
change as sources are added.

### Design Decisions

**D1. Three dedicated enrichment tables, not extensions of the history tables.**
Following the [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
precedent (which created a separate `MovieMetadata` rather than widening
`MovieHistory`), closed-loop state lives in new tables that are written off the
Pending→Commit critical path. `MovieHistory` / `TorrentHistory` stay pure
dedup/tracking tables.

```sql
-- Per selected torrent: its real fate after qB.
CREATE TABLE AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY,   -- computed from magnet at queue time
  href          TEXT NOT NULL,      -- FK → MovieHistory.Href
  video_code    TEXT,
  category      TEXT,               -- hacked_subtitle | hacked_no_subtitle | subtitle | no_subtitle
  state         TEXT NOT NULL,      -- queued | downloading | completed | in_library | stalled | failed
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT                -- run that queued it (provenance only, not a commit key)
);

-- Multi-source "what do I own" view (superset of RcloneInventory).
CREATE TABLE OwnershipLedger (
  video_code    TEXT NOT NULL,
  source        TEXT NOT NULL,      -- qb | nas | gdrive | pikpak
  category      TEXT,
  path          TEXT,
  size          INTEGER,
  present       INTEGER NOT NULL DEFAULT 1,
  observed_at   TEXT,
  PRIMARY KEY (video_code, source, category)
);

-- Per (server-instance × library): raw consumption signal, never merged on write.
CREATE TABLE ConsumptionSignal (
  video_code          TEXT NOT NULL,
  source_type         TEXT NOT NULL,   -- emby | plex
  instance            TEXT NOT NULL,   -- configured connection id, e.g. plex-home / emby-nas
  library_id          TEXT NOT NULL,
  library_name        TEXT,
  watched             INTEGER,
  progress_pct        INTEGER,
  play_count          INTEGER,
  rating              REAL,
  watched_at          TEXT,
  resolved_confidence TEXT,            -- high | medium | low
  observed_at         TEXT,
  PRIMARY KEY (video_code, instance, library_id)
);
```

**D2. `AcquisitionOutcome` is keyed by the qB hash, captured at queue time.**
When the uploader successfully adds a torrent, the system computes `qb_hash` from
the magnet (reusing the existing `extract_hash_from_magnet`) and writes a
`state=queued` row. This separates "selected" from "successfully queued" — a
distinction `TorrentHistory` cannot currently express — and gives the
reconciliation pass a stable join key back to qB and to `MovieHistory`.

```
queued ──→ downloading ──→ completed ──→ in_library
   │            │
   └────────────┴──→ stalled ──→ failed   (timeout / error / long-term no progress)
```

`in_library` is set when the `video_code` appears in `OwnershipLedger`
(gdrive/nas) — the real "it landed" evidence.

**D3. `completed` is a *push* signal captured at the cleanup step, not a poll.**
This is the load-bearing decision. Completed torrents are **deleted from qB**
(`remove_completed_torrents_keep_files` and the file-filter cleanup keep the
files but remove the torrent). A reconciliation pass that runs hours later will
frequently find the hash *already gone* from qB, so "still in qB?" cannot decide
completion. Instead, the cleanup step — which already enumerates completed
torrents — is instrumented to write `state=completed` for those hashes. The
reconciliation pass then derives `in_library` (via the Ledger) and
`stalled`/`failed` (still in qB with no progress, or past an N-day deadline with
neither `completed` nor `in_library`). The rejected alternative — high-frequency
polling to catch completion before deletion — is fragile and races the cleanup.

**D4. Reconciliation is a pure `Options → Result` service with read-only
collectors.** A new `javdb/ops/reconcile/` module exposes
`service.run(ReconcileOptions) -> ReconcileResult` with no argparse and no
`sys.exit`; `apps/cli/ops/reconcile.py` is the CLI adapter that owns process
concerns. Each external source is a **read-only `SourceCollector`** that produces
normalized `Observation`s and never writes; **all DB writes are centralized in
the service**. This is exactly the [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
seam shape. Tests target `Options → Result`, not live qB/Emby/Plex.

**D5. Dual trigger, bound to neither deployment.** The reconciliation loop is
scheduled by a new `ReconcileLibrary.yml` cron workflow (self-hosted runner, LAN
access to qB/Emby/Plex) **by default**; when the optional Docker API backend is
deployed, it may invoke the same `service.run(...)` in-process for near-real-time
reconciliation. Both call one implementation. If the Docker backend is not
running, the cron path is unaffected.

**D6. `OwnershipLedger` is a multi-source superset of `RcloneInventory`; dedup
reads the Ledger.** `RcloneInventory` is **not** torn down — it is retained as
the landing point for the `gdrive` collector, and Phase 2 reads it as the
`source='gdrive'` rows of the Ledger. The dedup checker is migrated to read the
Ledger so it sees all four sources (qb/nas/gdrive/pikpak), not just GDrive.

**D7. A pluggable `MediaServerAdapter` seam; Emby + Plex adapters; multi-instance
config.** `list_items(since) -> list[MediaItem]` is the contract; `EmbyAdapter`
(REST + API key) and `PlexAdapter` (X-Plex-Token) implement it and produce
normalized `MediaItem`s only. Media-server connections are configured as a
**list** (`MEDIA_SERVERS = [{type, instance, base_url, token, libraries?}, ...]`)
so multiple servers of the same type (e.g. two Plex) are first-class. Credentials
live in config/secrets and are masked via the existing masking module.

**D8. `ConsumptionSignal` is recorded at `(video_code, instance, library_id)`
granularity; merging is a derived view, never a destructive write.** The same
movie may have inconsistent state across servers and across libraries. Each
`(instance, library)` keeps its own row; **raw per-source signal is never
overwritten by another source.** "Did I watch it / what rating" is a *derived*
query (`watched = any`, `progress = max`, rating conflicts keep all source rows
with explicit-rating-and-most-recent preferred). Provenance — which instance,
which library said what — is permanently auditable.

**D9. Join-key resolution is best-effort with confidence and an explicit
`unresolved` bucket.** Media items carry filenames/paths, not video codes.
Resolution degrades by level: (1) reuse `filename_helper` / the Rust parser on
`file_path` → `high`; (2) regex fallback on title/folder → `medium`; (3) no match
→ an **`unresolved` bucket that is counted and `log()`-ged, never silently
dropped** (per the project's "no silent caps" rule). `resolved_confidence` is
persisted so a later preference model can weight by it.

**D10. Enrichment writes bypass session/rollback; idempotent UPSERT;
D1-canonical.** Closed-loop writes are recoverable enrichment: every write is an
UPSERT by the table's primary key, `last_seen_at` / `observed_at` refresh each
pass, and a failed write simply retries next pass. The loop never touches the
Pending→Commit path. `AcquisitionOutcome.session_id` is provenance only.

**D11. Scope is the *signal*, not the model.** This ADR produces
`ConsumptionSignal` and stops; consuming it for preference scoring is
[ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)'s
concern. The boundary is deliberate to keep this initiative shippable and
auditable.

## Consequences

### Positive

- **Truthful dedup** — distinguishes downloading / owned / never-tried / failed,
  across four ownership sources rather than a weekly GDrive snapshot.
- **Failures and stalls become visible** — an acquisition funnel
  (`queued → completed → in_library`) with explicit `stalled`/`failed` states.
- **The strongest implicit preference signal is captured** — real watch behavior
  from the servers already in use, unblocking the deferred preference model.
- **Additive growth** — new sources are new collectors; the service orchestration
  and the schema spine do not change.
- **Auditable provenance** — per-source, per-library raw signal is never lost.

### Negative

- **A new recurring job to operate** — `ReconcileLibrary.yml` needs a self-hosted
  runner with LAN access; the loop's freshness is bounded by its cron cadence.
- **Join-key ambiguity is permanent** — some media items will land in
  `unresolved`; the system surfaces the count but cannot guarantee 100% mapping.
- **More D1 surface** — three new tables to migrate, mirror, and reconcile.
- **`completed` capture couples to the cleanup step** — if cleanup logic changes,
  the completion observer must move with it (documented as a known coupling).

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Acquisition outcome | [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md) | `AcquisitionOutcome` table; `reconcile` service/CLI skeleton; `QbCollector`; queue-time `qb_hash` write; `completed` push at the cleanup step; `ReconcileLibrary.yml` cron | NAS/PikPak collectors; media servers |
| Phase 2 — Ownership truth | [IMP-ADR033-02](IMP-ADR033-02-ownership-truth.md) | `OwnershipLedger`; `GDrive`/`Nas`/`Pikpak` collectors; dedup migrated to read the Ledger; `in_library` derivation | Consumption signal |
| Phase 3 — Consumption signal | [IMP-ADR033-03](IMP-ADR033-03-consumption-signal.md) | `MediaServerAdapter` + Emby/Plex adapters; `ConsumptionSignal` at instance×library granularity; join-key resolution + `unresolved` bucket; multi-instance config | Preference model (ADR-025) |

Each phase ships and rolls back independently. Phase 1 is the foundation; Phases
2 and 3 are "add a collector" and do not alter the service orchestration.

**Planning cadence.** [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md)
(Phase 1) is implemented and locally verified. **IMP-ADR033-02 and
IMP-ADR033-03 are intentionally left as roadmap stubs** — their detailed plans
will be produced in a dedicated `grill-me` + `brainstorming` round after Phase 1,
so they can incorporate what the Phase 1 reconcile service and the
`AcquisitionOutcome` shape reveal in practice.

### Explicit non-goals (YAGNI)

- **No preference model** — produces `ConsumptionSignal` only (D11).
- **No playback-device-level tracking** — provenance stops at `(instance, library)`.
- **No realtime event stream / webhooks** in Phase 1 — cron is the default; the
  Docker in-process trigger is optional; qB/Plex webhooks are a future option.
- **No spider/parsing rewrite** — join-key reuses `filename_helper` / the Rust
  parser.
- **No change to session/rollback** — the loop is enrichment-only (D10).
- **No teardown of `RcloneInventory`** — Phase 2 wraps it as the `gdrive` source
  (D6).

## Domain Language (additions for CONTEXT.md)

- **Acquisition outcome** — the real fate of a selected torrent after qB
  (`queued → downloading → completed → in_library`, or `stalled` / `failed`).
- **Ownership ledger** — the multi-source view of what is actually owned, keyed by
  `(video_code, source, category)` across qb/nas/gdrive/pikpak.
- **Consumption signal** — per `(video_code, instance, library)` watch/rating
  evidence pulled from media servers; the strongest implicit preference signal.
- **Reconciliation pass** — the asynchronous job that collects observations from
  all sources and UPSERTs the closed-loop tables.
- **Collector** — a read-only source adapter that produces normalized
  observations and never writes.

## Alternatives Considered

- **Extend `TorrentHistory` with outcome columns / widen `RcloneInventory`** —
  rejected (D1): pollutes pure dedup/tracking tables and enlarges the
  Pending→Commit blast radius, the same reason ADR-022 created `MovieMetadata`.
- **Poll qB frequently to catch `completed` before deletion** — rejected (D3):
  fragile, races the cleanup step, and still misses slow downloads.
- **Merge Emby/Plex signal into one row on write** — rejected (D8): destroys
  per-source provenance and makes cross-server conflicts unauditable.
- **Cron-only or Docker-only execution** — rejected (D4/D5): cron-only loses
  near-real-time when a backend is present; Docker-only binds the loop to an
  optional always-on deployment.

## References

- [ADR-022 — User Preference Data Foundation](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-024 — Torrent Quality Evidence Foundation](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-010 — D1 Access Port](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-028 — Web Platform & Capability Completeness Roadmap](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
- 2026-05-29: IMP-ADR033-01 (Phase 1) plan written; IMP-02/03 deferred to a
  post-Phase-1 `grill-me` + `brainstorming` round. Web surface split out to
  [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md).
- 2026-05-30: IMP-ADR033-01 (Phase 1) implemented and locally verified. Remote
  D1 apply and local SQLite mirror refresh remain deployment-environment gates.
