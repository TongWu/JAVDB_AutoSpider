# BFR-018: Explore one-click qB add silently dropped pause and rename

**Status**: Fixed
**Date**: 2026-06-14
**Severity**: Medium
**Affected**: `apps/api/services/explore_service.py`, `javdb/integrations/qb/client.py`
**Related**: [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)

---

## Symptom

The web "explore → one-click add to qBittorrent" flow silently ignored two user
settings on **every** qB server version:

- `AUTO_START=false` had no effect — torrents always started downloading
  immediately instead of being added paused.
- The custom torrent title passed from the explore UI was never applied — qB
  showed the torrent under its default name.

No error was raised; the add returned HTTP 200 and appeared to succeed. The
daily `qb_uploader` / `qb_file_filter` paths were unaffected.

## Root Cause

`explore_service._qb_add_magnet` hand-rolled the `POST /api/v2/torrents/add`
request as a **partial copy** of `QBittorrentClient.add_torrent`
(`javdb/integrations/qb/client.py`). The two copies drifted: explore's payload
used the field names `addPaused` and `name`, but qBittorrent's WebAPI names
those fields `paused` and `rename`. qB **silently ignores unknown form fields**,
so both intents were dropped with no signal.

The canonical `add_torrent` already used the correct names and even documents
them (`paused` not `addPaused`; `rename` not `name`, citing qB issue #22766) —
but explore never called it, so the correction never reached this path.

The underlying design flaw is the **duplication of the add-torrent wire
contract**: two copies of the same HTTP request, only one kept correct. A
contract with two homes drifts; the integration boundary (ADR-015) exists
precisely so qB API details live in one place.

## Fix

`_qb_add_magnet` now delegates to the single canonical implementation:

```python
client = QBittorrentClient.from_existing_session(session, base_url, request_timeout=...)
client.session.verify = verify_tls   # re-assert this request's QB_VERIFY_TLS
client.add_torrent(magnet, name=title, category=..., save_path=...,
                   auto_tmm=True, skip_checking=..., paused=not AUTO_START)
```

- Reuses `add_torrent`'s correct `paused` / `rename` field names — both intents
  now take effect.
- `from_existing_session` aligns `session.verify` with the *global*
  `qb_verify_tls()`, so the per-request `QB_VERIFY_TLS` is re-asserted afterward
  to preserve the explore flow's per-request transport behavior.
- Added `tests/unit/test_explore_qb_add_magnet.py` pinning the corrected wire
  payload (`paused`/`rename` present, `addPaused`/`name` absent), which had **no
  prior test coverage** — the gap that let the drift go unnoticed.

## Side Effects

Intended behavior change on the explore add path only:

- `AUTO_START=false` now actually adds the torrent paused.
- A custom title now actually renames the torrent.
- The add payload now also carries qB-default fields (`contentLayout=Original`,
  `ratioLimit=-2`, `seedingTimeLimit=-2`, `downloadPath=""`) from the shared
  client, matching the `qb_uploader` path. These are qB defaults — no functional
  change.

The daily `qb_uploader` / `qb_file_filter` paths are untouched (they already
used the canonical client).

## Follow-Up

- [x] Consolidate explore's qB add into `QBittorrentClient.add_torrent`.
- [ ] Continue decomposing the `explore_service` god-object (fetch-half +
      magnet scoring) per the architecture review — the qB concern was the
      highest-value slice and is done; the fetch concern is the next extraction.
