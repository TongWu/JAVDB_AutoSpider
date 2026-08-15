# API Reference

This page lists the HTTP endpoints exposed by `apps/api`. The authoritative machine-readable schema is [`docs/api/openapi.json`](../../../api/openapi.json); generated TypeScript types in the frontend repo (`javdb-autospider-web`) are derived from it.

For parsing-focused REST usage (page parsing, etc.), see [api-usage-guide.md](api-usage-guide.md).

## Phase 1 Frontend Console Endpoints

These endpoints were added in 2026-05 to support the new web console (`javdb-autospider-web`).

### Discovery

- `GET /api/capabilities` — runtime feature flags + version info. Used by the FE to gate UI per deployment. See [openapi.json](../../../api/openapi.json) for the full shape.

### Onboarding

- `GET /api/onboarding/status` — returns `{completed, required_missing[], skippable_missing[]}`.
- `POST /api/onboarding/test` — tests one component (`javdb`/`qb`/`proxy`/`smtp`); returns `{component, ok, message, details?}`.
- `POST /api/onboarding/complete` — admin-only; marks setup done.
- `POST /api/onboarding/dismiss-hint` — admin-only; dismisses a Dashboard hint card.

### Generic state

- `GET /api/system/state?key=...` — reads a KV pair from `system_state`.
- `PUT /api/system/state` — admin-only; writes a KV pair.

### Torrent quality

- `GET /api/quality/evaluations?limit=&movie_href=` — authenticated, read-only list of ADR-024 shadow quality evaluations. When `movie_href` is omitted, returns recent evaluations.
- `GET /api/quality/evidence/{info_hash}` — authenticated, read-only torrent-level evidence for the `production_download` role.

The following three endpoints are added by ADR-024 IMP-08 (assist mode, 2026-06-19). They are active only when `TORRENT_QUALITY_POLICY_MODE=assist`; the underlying evaluation rows are populated by the gated assist evaluator.

- `GET /api/quality/recommendations?movie_href=` — authenticated, read-only. Per category, returns the current production choice and the `shadow_rank=1` recommended candidate with a reason-code diff. Response shape: `{items: [{javdb_category, current, recommended, reason_diff}]}`.
- `GET /api/quality/needs-review?limit=` — authenticated, read-only. Returns evaluations where `decision='needs_review'` or `would_replace_current_choice=true`. `limit` defaults to 50, capped at 200; `limit<=0` → 400.
- `POST /api/quality/review-labels` — admin-only. Body: `{info_hash, movie_href, scoring_version, label, note?}` where `label ∈ accept | reject | skip`. Records an operator decision via `TorrentQualityReviewRepo` (the labelled dataset Phase 3 tunes thresholds against). Returns `{status: "recorded"}`. Invalid `label` → 422. `reviewed_at` is stamped server-side; `reviewer` is read from the JWT subject.

### User intent and discovery

These endpoints are dual-backend: the Python FastAPI surface and the
Cloudflare Worker mirror expose the same shapes. UI rendering is gated by
`capabilities.features.watch_intent` and `capabilities.features.subscriptions`.

- `GET /api/subscriptions?active_only=&limit=&offset=` — authenticated list of followed actors (`ActorSubscription`).
- `PUT /api/subscriptions/{actor_href}` — admin-only; follow or reactivate an actor with body `{actor_name?, active}`. The stored key is the normalized `/actors/<id>` href.
- `GET /api/subscriptions/{actor_href}` — authenticated detail for one followed actor.
- `DELETE /api/subscriptions/{actor_href}` — admin-only; unfollow an actor.
- `GET /api/new-works?actor_href=&include_dismissed=&limit=&offset=` — authenticated feed of newly discovered works from followed actors.
- `POST /api/new-works/{video_code}/dismiss` — admin-only; hide a discovered work from the default feed.

### Sessions

- `GET /api/sessions?state=&cursor=&limit=` — cursor-paginated list of ReportSessions.
- `GET /api/sessions/{session_id}` — full session detail incl. writes.
- `POST /api/sessions/{session_id}/rollback` — admin-only; body `{dry_run, include_pending, restore_from_audit}`.
- `POST /api/sessions/{session_id}/commit` — admin-only; body `{force, drop_pending, fanout_claims, emit_metrics}`. `fanout_claims` and `emit_metrics` default to `true` so the HTTP path matches the CLI's full-parity commit (MovieClaim coordinator fanout + `pending_session_verify` JSONL emission); pass `false` to opt into a DB-only commit.

### D1 migrations

Admin-only. Python backend only — the TypeScript Worker does not mirror this surface.

- `GET /api/migrations` — lists `javdb/migrations/d1/*.sql` with applied state, read from the `migration_applied:<id>` keys in `system_state`.
- `POST /api/migrations/{id}/run` — body `{dry_run, acknowledge_unrecorded}`, both defaulting to `true` and `false` respectively.
  - `dry_run=true` returns `{sql_preview, statements}`; `statements` counts executable statements, excluding comments. Never needs `acknowledge_unrecorded` — a preview changes nothing.
  - `dry_run=false` applies the migration to D1 and records it, returning `applied: true`. Also requires `acknowledge_unrecorded: true` for any migration without a ledger marker, which is every migration this endpoint did not itself apply — see `migrations.unrecorded` below.

The runner resolves the target database from the migration's own `wrangler d1 execute javdb-<history|reports|operations>` header line, and **refuses rather than guesses** when that line is absent or names more than one database (`400 migrations.target_db_unresolved`).

Refusals, all raised before any statement runs. The ones that depend only on the file itself — `target_db_unresolved`, `empty`, `not_atomic`, `pragma_unsupported`, `drop_table_unsupported` — are checked *before* `unrecorded`, so a malformed migration reports what is actually wrong instead of teaching operators to set the acknowledgement reflexively:

| Code | Status | Meaning |
| --- | --- | --- |
| `migrations.backend_not_d1` | 409 | `STORAGE_BACKEND` is not `d1`/`dual`. |
| `migrations.already_applied` | 409 | Recorded as applied; replaying a non-idempotent `ALTER TABLE ADD COLUMN` would fail halfway. |
| `migrations.unrecorded` | 409 | The migration has no marker, which means "not applied *through this endpoint*", not "not applied". Resend with `acknowledge_unrecorded: true` once you have checked it against the live schema. |
| `migrations.applied_state_unreadable` | 503 | The ledger could not be read, so "already applied?" is unknown. Fails closed — treating unknown as "not applied" is the dangerous guess. |
| `migrations.target_db_unresolved` | 400 | No unambiguous `wrangler d1 execute javdb-<db>` header. |
| `migrations.unparseable` | 400 | Unterminated string literal — the splitter cannot tell where the statement ends, and would ship a half statement. |
| `migrations.empty` | 400 | No executable statements. |
| `migrations.not_atomic` | 400 | More statements than one D1 batch holds (50), so the script could only be applied in chunks — i.e. non-atomically. Use Wrangler. |
| `migrations.pragma_unsupported` | 400 | The script contains a `PRAGMA`, which is a no-op inside the transaction this runner batches into. `2026_05_13_session_id_to_text_reports.sql` is the shipped example. Use Wrangler. |
| `migrations.drop_table_unsupported` | 400 | The script `DROP`s a table. Inside the batch, foreign keys cannot be relaxed, so any row still referencing it fails the whole batch — the table-rebuild migrations (`2026_05_13_session_id_to_text_*`, `2026_06_16_newworks_composite_pk`) are the shipped examples. Refused categorically, not per reference graph. Use Wrangler. |
| `migrations.claim_failed` | 503 | The ledger entry could not be reserved, so a concurrent apply cannot be ruled out. Nothing ran. |
| `migrations.connection_failed` | 503 | The D1 client could not be opened — missing credentials, typically. Nothing was sent, so the claim is released. |
| `migrations.ledger_absent` | 409 | `system_state` does not exist yet, so nothing can be claimed. Only the migration that creates it may run in that state; apply `0042_system_state_table` first. |

The statements are submitted as **one D1 batch**, which is atomic: they land whole or roll back whole. The ledger entry is claimed with a plain `INSERT` *before* execution — `system_state.key` is the primary key, so two admins racing the same migration are serialized by D1 rather than by a read-then-write window, and the loser gets `already_applied`.

Three failure codes come *after* execution starts, and the first two differ in exactly one respect — whether D1 told us what happened:

- `502 migrations.execution_failed` — **nothing landed**, established rather than assumed. Either no request left the process at all (an open circuit rejects before the POST), or exactly one went out and D1 rejected it, which the atomic batch rolls back whole. The claim is released and the migration lists as unapplied again, so it is safe to retry once the cause is fixed. If the release itself failed, the message says so explicitly — clear the marker before retrying.
- `502 migrations.outcome_unknown` — a request went out and its fate cannot be established: a timeout, a dropped connection, an unclassified error, a batch that returned fewer results than it had statements, **or a rejection that arrived after the transport re-sent the batch**. That last one matters: the transport retries transient failures on its own and a migration batch is not idempotent, so a first attempt that committed and lost its response produces a second attempt rejected with something like `duplicate column name` — a permanent error describing the retry, not the migration. The two 502s are told apart by the port's sent-request counter, not by the exception type. D1 may have committed the batch and lost the response on the way back, so this is **not** a rollback. The claim is deliberately **kept**, which makes the migration read as applied and blocks an accidental replay. Check the live schema: if the change landed, leave the marker; if it did not, clear it and re-run.
- `500 migrations.record_failed` — only reachable for the bootstrap migration that CREATEs `system_state` itself, which has no ledger to claim against beforehand and is therefore recorded afterwards. The schema change already landed: **do not re-run it**, record it manually instead.

> **`applied` means "applied through this endpoint", not "present in D1".** The `migration_applied:` keys are only ever written here, so a migration run with `wrangler d1 execute` — which is how every historical migration was applied — still lists as unapplied. That is why `acknowledge_unrecorded` is demanded **per migration** rather than only while the ledger is empty: gating on an empty ledger would protect exactly one request, and the moment the first API apply wrote a marker every remaining wrangler-applied file would lose the speed bump while still listing as unapplied. Several of those files are destructive — the `2026_05_13_session_id_to_text_*` set rebuilds tables from a hardcoded column list and `DROP`s the originals, so replaying one today would discard every column added since. Unrecorded means unknown, and unknown is answered by a human, once, per file.

### Diagnostics — site-contract drift (ADR-035)

Read-only surface over the site-contract drift sentinel. Gated by `capabilities.features.site_drift_sentinel` (the frontend hides the drift panel when it is `false`).

- `GET /api/diag/ops-incidents?incident_type=site_drift` — filter persisted ops incidents by type; `incident_type=site_drift` returns drift incidents specifically (also accepts `status`, `run_id`, `session_id`, `confidence`, `limit`).
- `GET /api/diag/parse-field-health` — latest committed per-field parse health. Response:

  ```json
  { "items": [ { "page_type": "index", "field": "href", "severity": "critical",
                 "fill_rate": 0.99, "sample_count": 120, "observed_at": "...",
                 "baseline": null, "threshold": 0.99, "status": "ok" } ] }
  ```

  `status ∈ ok | critical_drift | soft_drift | no_baseline | insufficient_sample`.

### Test mode (E2E only)

- `POST /api/test/reset` — present only when the server is started with `TEST_MODE=1`. Truncates ops/history tables. **Must never be enabled in production.**
- `POST /api/test/seed-sessions` — present only when the server is started with `TEST_MODE=1`. Idempotently seeds three deterministic sessions (`test-committed-001`, `test-finalizing-002`, `test-inprogress-003`) covering committed/audit, finalizing/pending, and in_progress/audit lifecycles so real-data E2E rollback specs have predictable fixtures. Response: `{seeded, session_ids}`.
