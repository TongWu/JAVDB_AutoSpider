# ADR-038: Agentic Operator MCP Surface

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; execution delegated to per-phase IMPs            |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 4 — an agentic operator console).

## Context

The system is operated through a Vue console + REST API and a CLI. There is **no
conversational/agent interface**: to answer "why did last night's run only find
three movies?" an operator stitches together the workflow result, session
lifecycle, D1 drift, the email summary, and runbook pages by hand —
[ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
added read-only AI diagnosis but only as a single endpoint, not a surface an agent
can explore.

Two facts make an MCP surface cheap and timely:

1. **The service layer is already well-factored.** `apps/api/services/`
   (`task_service`, `spider_jobs`, `explore_service`, `sessions`, `system_service`,
   `config_service`, …) is adapted by both the FastAPI routers and the CLI. A new
   **MCP adapter** is the third adapter over the *same* services — exactly the
   [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
   one-service/many-adapters shape. No MCP server exists yet (clean slate).
2. **This session just built the data an agent wants to read.** The event spine
   ([ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)),
   incidents ([ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)),
   acquisition outcomes
   ([ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)), and drift
   ([ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md))
   are exactly the surface that lets an agent answer "what happened in this run?"

This ADR exposes the system as a **conversational agent surface via MCP**, starting
**read-only** (mirroring ADR-026's deliberate read-only-first arc), with gated
actions deferred to a later phase.

## Decision

Build `apps/mcp/`: a **Python FastMCP** server that is a thin **third adapter** over
the existing `apps/api/services/` layer. Phase 1 exposes **read-only** tools
(observe + diagnose) over stdio; mutating actions are deferred to Phase 2 behind an
explicit dry-run + confirm + audit gate.

### Design Decisions

**D1. The MCP server is a third adapter over the service layer — not a
reimplementation.** Each tool is a thin call into an existing service/repo function;
the API routers and the MCP tools call the **same** services. If a needed read has no
service function, it is added to the **service layer** (shared by API + MCP), never
inlined into the MCP adapter. This holds the
[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
boundary: one service, three adapters (CLI / API / MCP).

```
apps/mcp/
  server.py     # FastMCP instance + tool registration
  tools/        # one thin module per tool group; each calls a service/repo
  context.py    # reuses apps/api/services runtime/context
```

**D2. Python FastMCP, stdio transport in Phase 1.** FastMCP reuses the Python
service layer directly and gives full local capability. Phase 1 ships **stdio** so
the operator connects it to a local agent (e.g. Claude); remote HTTP/SSE transport is
a later phase.

**D3. Phase 1 is read-only: observe + diagnose.** The tool taxonomy:

| Tool | Backed by | Answers |
| --- | --- | --- |
| `list_runs` / `get_run` | `task_service` / jobs | recent runs, status |
| `list_sessions` / `get_session` | sessions service | session state / lifecycle |
| `search_history` | `explore_service` / history | "do I have X?" |
| `get_run_timeline` / `query_events` | `PipelineEvent` (ADR-036) | "what happened in this run?" |
| `list_incidents` / `get_incident` | `OpsIncidents` (ADR-026) | operational incidents |
| `diagnose_run` | ADR-026 diagnosis (read-only) | "why did it fail?" |
| `get_acquisition_outcomes` | `AcquisitionOutcome` (ADR-033) | torrents landed / stalled |
| `get_drift` | `site_drift` / `ParseFieldHealth` (ADR-035) | parser drift |
| `get_capabilities` | capabilities | deployment capability |

Composing `query_events` + `list_incidents` + `get_acquisition_outcomes` lets the
agent answer multi-source operational questions in one turn.

**D4. Gated actions are deferred to Phase 2, and their gate is specified now.** A
mutating tool (`trigger_run`, `rollback_session`, `commit_session`) must:
(1) return a **dry-run preview** of what it would do; (2) require an explicit
`confirm=true` second call to execute; (3) reuse the existing auth; (4) write an
**audit event** (`PipelineEvent` / `OpsIncident`) for every execution. This mirrors
ADR-026's read-only → gated-remediation progression.

**D5. Safety: read-only, masked, no secrets.** Phase 1 tools never mutate; sensitive
values are masked via the existing masking module; **`config.py`/secrets are never
exposed as tools**. Local stdio assumes a trusted operator; remote transport (later)
adds auth at the transport.

**D6. No TypeScript Worker MCP in Phase 1.** A parallel Cloudflare Worker MCP (the
"dual-MCP" mirror of the ADR-017 backend split) is a later, optional phase; Phase 1
is the Python adapter only.

## Consequences

### Positive

- **A conversational operator surface** — ask the system questions instead of
  stitching console pages together.
- **Cheap — a thin adapter** — reuses the existing service layer per ADR-015; little
  new logic.
- **Surfaces this session's work** — the event spine, incidents, outcomes, and drift
  become agent-queryable on day one.
- **Safe by construction** — read-only Phase 1; actions are a separately-gated phase.

### Negative

- **A new adapter to keep in sync** — when a service signature changes, the MCP tool
  (like the API router) must follow.
- **Service-gap pressure** — some reads may lack a service function and require one to
  be added (a benefit for the API too, but upfront work).
- **Trust model is transport-dependent** — stdio assumes a trusted local operator;
  remote use needs the later auth'd transport.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Read-only surface | [IMP-ADR038-01](IMP-ADR038-01-readonly-mcp.md) | `apps/mcp/` FastMCP server (stdio); the read-only tools above; `diagnose_run` reusing ADR-026 | Mutating actions; remote transport; TS Worker MCP |
| Phase 2 — Gated actions | IMP-ADR038-02 (stub) | `trigger_run` / `rollback_session` / `commit_session` behind dry-run + confirm + auth + audit-event | — |
| Phase 3 — Remote / dual MCP (optional) | IMP-ADR038-03 (stub) | HTTP/SSE transport; a parallel TS Worker MCP | — |

Phase 1 stands alone (read-only, additive). Phase 2 adds the gated mutating surface.
Phase 3 is optional remote/serverless reach.

### Explicit non-goals (YAGNI)

- **No mutations in Phase 1** — observe + diagnose only.
- **No remote transport in Phase 1** — stdio only.
- **No TS Worker MCP** — the dual-MCP mirror is Phase 3.
- **No secrets/config tools** — never exposed.
- **No reimplementation of service logic** in the MCP adapter (D1).

## Domain Language (additions for CONTEXT.md)

- **MCP adapter** — the `apps/mcp/` surface that exposes the service layer as MCP
  tools, the third adapter alongside CLI and API.
- **Read-only tool** — an MCP tool that only queries; the whole of Phase 1.
- **Gated action** — a mutating MCP tool guarded by dry-run preview + explicit
  confirm + auth + audit event (Phase 2).

## Alternatives Considered

- **TypeScript Worker MCP first** — rejected (D2/D6): the rich Python service layer is
  the cheapest reuse and gives full local capability; the Worker MCP is a later
  optional mirror.
- **Read + actions in Phase 1** — rejected (D3/D4): mutating tools can trigger or roll
  back production; they need the deliberate dry-run/confirm/audit gate and a separate
  phase, exactly as ADR-026 sequenced read-only before gated remediation.
- **Reimplement queries inside the MCP adapter** — rejected (D1): violates ADR-015 and
  forks logic away from the API.

## References

- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
