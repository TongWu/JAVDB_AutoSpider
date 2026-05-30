# ADR-039: Pluggable Integration Platform

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; execution delegated to per-phase IMPs            |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md), [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 6 — a pluggable extension platform).

## Context

[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
turned the integrations (`qb`, `pikpak`, `rclone`, `notify`, `gh_actions`) into
`Options → Result` services, but each is **hardcoded to one backend**:
`javdb/integrations/notify/` contains only `email/` — there is no backend
abstraction, so a Telegram or Discord notifier cannot be added without surgery.
The pipeline wires integrations by name (`apps.cli.qb.uploader`,
`apps.cli.pikpak.bridge`), and **no plugin/registry/entry-point mechanism exists**
(clean slate).

This blocks turning "Ted's bespoke system" into a **self-hostable platform** others
can extend — swapping qB for Transmission, email for Telegram, GDrive for Plex.
ADR-015 already gave each integration a clean service boundary; the missing piece
is a **registry + per-category plugin contract** so a category can host multiple
interchangeable backends selected by config.

This ADR introduces that platform, **registry-based and entry-point-ready**, and
proves it on the `notify` category (email as a built-in plugin + a new Telegram
plugin).

## Decision

Add `javdb/integrations/plugins/` (a registry) and a per-category plugin contract,
starting with `notify`. Built-in plugins register at import; config selects the
active backend(s); a dispatcher fans out to them with failure isolation. The
registry is designed so true third-party entry-point discovery is an additive
Phase 2.

### Design Decisions

**D1. In-repo registry, entry-point-ready — not entry-points yet.** A
`PluginRegistry` holds plugins keyed by `(category, name)`. Built-in plugins
register at import time; `config` selects which are active. The registry exposes a
seam (`discover_entry_points(group)`) so a Phase-2 `importlib.metadata` discovery of
third-party pip-installable plugins is purely additive — Phase 1 registers only
built-ins. This is the low-risk path to a platform: config-swap built-ins now, grow
to third-party later.

**D2. A plugin contract per category; `notify` first.** Each category defines a
`Protocol`. For notify:

```python
class NotifyPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def send(self, message: NotifyMessage) -> NotifyResult: ...
```

**D3. The existing email is wrapped as a built-in plugin — no rewrite.**
`EmailNotifyPlugin` is a thin `name='email'` adapter over the existing
`notify/email/service.py` (its internals are untouched). A new
`TelegramNotifyPlugin` (`name='telegram'`) calls the Telegram Bot API. Each plugin
reads its own config (email → `SMTP_*`; telegram → `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID`). `NOTIFY_BACKENDS` selects the active list and **defaults to
`['email']`**, so existing behaviour is preserved; adding `'telegram'` enables a
second route.

**D4. Dispatch fans out with failure isolation.** `notify.send(message)` iterates
the active plugins, calls each `.send()`, and collects `NotifyResult`s; one
backend's failure (e.g. Telegram down) does not block the others (e.g. email).
Existing callers (the pipeline's email summary) route through `notify.send` and
keep working.

**D5. Module shape.**

```
javdb/integrations/plugins/registry.py       # PluginRegistry (+ entry-point seam)
javdb/integrations/notify/plugin.py          # NotifyPlugin protocol + NotifyMessage/Result
javdb/integrations/notify/dispatch.py        # notify.send() fan-out
javdb/integrations/notify/email/plugin.py    # EmailNotifyPlugin (wraps existing service)
javdb/integrations/notify/telegram/plugin.py # TelegramNotifyPlugin (new)
```

**D6. Plugins compose with this session's work (future).** A plugin can later be an
[ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
event consumer (notify on `SessionFailed`) and surface as an
[ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
MCP tool — making the plugin layer the place the session's spine/console converge
into an ecosystem. Out of Phase 1 scope, recorded as direction.

**D7. Phased.** Phase 1 proves the contract on `notify`; Phase 2 adds entry-point
discovery (true third-party) and a second category (`downloader`: qB +
Transmission); Phase 3 adds media-server/destination categories and the
event-consumer / MCP-tool composition.

## Consequences

### Positive

- **A real extension point** — categories host interchangeable backends selected by
  config; adding a notifier is a plugin, not surgery.
- **Backward-compatible** — `NOTIFY_BACKENDS` defaults to email; nothing changes
  until a self-hoster opts in.
- **Low risk, grows up** — built-in registry now, third-party entry points later,
  same contract.
- **Builds on ADR-015** — each plugin is the `Options → Result` service the boundary
  already defines.
- **Self-hostable platform trajectory** — others can extend qB→Transmission,
  email→Telegram, GDrive→Plex.

### Negative

- **A contract to keep stable** — once third parties depend on `NotifyPlugin`
  (Phase 2), its shape must version carefully.
- **Dispatch semantics to define** — fan-out + failure isolation + result
  aggregation add a small layer over the previously-direct email call.
- **More config surface** — `NOTIFY_BACKENDS` + per-plugin keys.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Registry + notify | [IMP-ADR039-01](IMP-ADR039-01-notify-plugins.md) | `plugins/registry`; `NotifyPlugin` contract; `EmailNotifyPlugin` (wraps existing); `TelegramNotifyPlugin` (new); `NOTIFY_BACKENDS` config; `notify.send` fan-out | entry-point discovery; other categories |
| Phase 2 — Entry points + downloader | IMP-ADR039-02 (stub) | `discover_entry_points` (third-party); `downloader` category (qB + Transmission) | — |
| Phase 3 — Ecosystem (optional) | IMP-ADR039-03 (stub) | media-server/destination categories; plugins as ADR-036 consumers / ADR-038 tools | — |

Phase 1 stands alone and is backward-compatible. Phases 2/3 widen the platform.

### Explicit non-goals (YAGNI)

- **Only the `notify` category in Phase 1** — qB/rclone/pikpak untouched.
- **No entry-point discovery in Phase 1** — built-in registration only (the seam is
  reserved).
- **No rewrite of the email internals** — wrapped as a built-in plugin (D3).
- **No event-consumer / MCP composition in Phase 1** — recorded as Phase 3 direction.

## Domain Language (additions for CONTEXT.md)

- **Plugin** — a registered backend implementing a category's contract (e.g. a
  `NotifyPlugin`).
- **Plugin registry** — `javdb/integrations/plugins/registry.py`, keyed by
  `(category, name)`, registering built-ins now and (Phase 2) entry-point
  third-parties.
- **Plugin contract** — the per-category `Protocol` a plugin satisfies.
- **Built-in plugin** — a plugin shipped in-repo (e.g. `EmailNotifyPlugin`).
- **Notify backend** — an active notify plugin selected via `NOTIFY_BACKENDS`.

## Alternatives Considered

- **Python entry points from the start** — rejected (D1): the "real platform" but
  requires upfront packaging/versioning/contract-stability; the registry reaches
  the same place additively with far less risk.
- **Directory-drop plugins** — rejected (D1): less standard than entry points and no
  simpler than the registry.
- **Rewrite email into the plugin shape** — rejected (D3): the email service already
  works behind an ADR-015 boundary; wrap it, don't rewrite it.

## References

- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [ADR-038 — Agentic Operator MCP Surface](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
