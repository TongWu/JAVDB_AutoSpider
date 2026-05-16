# API Reference

This page lists the HTTP endpoints exposed by `apps/api`. The authoritative machine-readable schema is [`docs/api/openapi.json`](../../api/openapi.json); generated TypeScript types in the frontend repo (`javdb-autospider-web`) are derived from it.

For parsing-focused REST usage (page parsing, etc.), see [api-usage-guide.md](api-usage-guide.md).

## Phase 1 Frontend Console Endpoints

These endpoints were added in 2026-05 to support the new web console (`javdb-autospider-web`).

### Discovery

- `GET /api/capabilities` — runtime feature flags + version info. Used by the FE to gate UI per deployment. See [openapi.json](../../api/openapi.json) for the full shape.

### Onboarding

- `GET /api/onboarding/status` — returns `{completed, required_missing[], skippable_missing[]}`.
- `POST /api/onboarding/test` — tests one component (`javdb`/`qb`/`proxy`/`smtp`); returns `{component, ok, message, details?}`.
- `POST /api/onboarding/complete` — admin-only; marks setup done.
- `POST /api/onboarding/dismiss-hint` — admin-only; dismisses a Dashboard hint card.

### Generic state

- `GET /api/system/state?key=...` — reads a KV pair from `system_state`.
- `PUT /api/system/state` — admin-only; writes a KV pair.

### Sessions

- `GET /api/sessions?state=&cursor=&limit=` — cursor-paginated list of ReportSessions.
- `GET /api/sessions/{session_id}` — full session detail incl. writes.
- `POST /api/sessions/{session_id}/rollback` — admin-only; body `{dry_run, include_pending, restore_from_audit}`.
- `POST /api/sessions/{session_id}/commit` — admin-only; body `{force, drop_pending}`.

### Test mode (E2E only)

- `POST /api/test/reset` — present only when the server is started with `TEST_MODE=1`. Truncates ops/history tables. **Must never be enabled in production.**
