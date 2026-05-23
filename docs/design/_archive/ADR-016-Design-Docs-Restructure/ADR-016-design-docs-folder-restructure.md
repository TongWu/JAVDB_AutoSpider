# ADR-016: docs/design/ Folder Restructure

**Status:** Completed
**Date:** 2026-05-22
**Author:** Ted
**Related Implementation Plans:** [IMP-ADR016-01](IMP-ADR016-01-design-docs-folder-restructure.md) (single-phase restructure)

## Context

The current `docs/design/` layout places ADRs, IMPs, and BFRs in parallel sibling directories (`adr/`, `impl/`, `bfr/`). This creates three problems:

1. **Conceptual mismatch** — IMPs are subordinate to ADRs, but the flat layout treats them as equals. Finding all plans for an ADR requires jumping between `adr/` and `impl/`.
2. **Cross-reference complexity** — Every ADR↔IMP link requires `../impl/` or `../adr/` relative paths. These break when files are archived since the archive directories add an extra level.
3. **Archival friction** — Archiving an ADR requires moving the ADR file AND separately tracking/moving its IMPs. Internal cross-references break and must be manually updated.

## Decision

Restructure `docs/design/` so each ADR and BFR gets its own folder, with IMPs co-located inside their parent ADR folder.

### Design Decisions

D1. **Per-ADR folders** — Each ADR gets a folder named `ADR-NNN-Pascal-Kebab-Summary/` (e.g. `ADR-010-D1-Access-Port/`). The folder contains the ADR `.md` + `.zh.md` and all its IMP files. BFRs follow the same pattern with `BFR-NNN-Pascal-Kebab-Summary/`.

D2. **Co-located IMPs** — IMP files live inside their parent ADR folder. Cross-references between an ADR and its IMPs become filename-only links (no relative path prefixes). Cross-ADR references use `../ADR-NNN-Foo/` pattern.

D3. **Whole-folder archival** — Completed ADRs (status Completed/Superseded + all IMPs done) archive by moving the entire folder to `_archive/ADR-NNN-Foo/`. Internal links (ADR↔IMP) need no changes. Only external inbound references need `_archive/` inserted.

D4. **Completed IMPs in active ADRs** — Completed IMPs stay in their parent ADR folder. Completion is tracked by the `Status:` field inside the file, not by directory location.

D5. **Templates directory** — `_templates/` holds `ADR-TEMPLATE.md`, `ADR-TEMPLATE.zh.md`, `BFR-TEMPLATE.md`, `BFR-TEMPLATE.zh.md`.

D6. **Architecture unchanged** — `docs/design/architecture/` is not affected by this restructure.

D7. **Active vs Archived classification** — ADR-001 through 007 are archived. ADR-008 through 015 are active. BFR-001 is active. ADR-016 is archived after this restructure completes.

### Target Structure

```text
docs/design/
├── ADR-008-Frontend-Rewrite/
│   ├── ADR-008-frontend-rewrite-architecture.md
│   ├── ADR-008-frontend-rewrite-architecture.zh.md
│   ├── IMP-ADR008-01-frontend-phase1-backend-prerequisites.md
│   ├── IMP-ADR008-02-frontend-phase1-completion.md
│   ├── IMP-ADR008-03-frontend-phase2-full-cli-coverage.md
│   └── IMP-ADR008-04-frontend-phase3-power-user.md
├── ADR-009-D1-Drift-Classifier/
├── ADR-010-D1-Access-Port/
├── ADR-011-Parsing-Module/
├── ADR-012-Pipeline-Run-Boundary/
├── ADR-013-Runner-Runtime-State/
├── ADR-014-Storage-Cli-Layering/
├── ADR-015-Integrations-Interface/
├── BFR-001-Login-Proxy-Mismatch/
├── _archive/
│   ├── ADR-001-Split-Db-Module/
│   ├── ADR-002-Observability-Storage/
│   ├── ADR-003-Metrics-Pipeline/
│   ├── ADR-004-Proxy-Discovery/
│   ├── ADR-005-Db-Py-Retirement/
│   ├── ADR-006-Pending-Mode-Rollout/
│   ├── ADR-007-Monorepo-Restructure/
│   └── ADR-016-Design-Docs-Restructure/
├── _templates/
│   ├── ADR-TEMPLATE.md
│   ├── ADR-TEMPLATE.zh.md
│   ├── BFR-TEMPLATE.md
│   └── BFR-TEMPLATE.zh.md
└── architecture/
```

### Cross-Reference Path Rules

| Reference direction | Path pattern |
| --- | --- |
| ADR → own IMP | `IMP-ADR010-01-*.md` (same directory) |
| IMP → own ADR | `ADR-010-*.md` (same directory) |
| ADR → other active ADR | `../ADR-012-Pipeline-Run-Boundary/ADR-012-*.md` |
| ADR → archived ADR | `../_archive/ADR-007-Monorepo-Restructure/ADR-007-*.md` |
| IMP → other ADR's IMP | `../ADR-012-Pipeline-Run-Boundary/IMP-ADR012-01-*.md` |
| Archived → active | `../../ADR-010-D1-Access-Port/ADR-010-*.md` |
| Archived → archived | `../ADR-005-Db-Py-Retirement/ADR-005-*.md` (within `_archive/`) |
| CLAUDE.md → IMP | `docs/design/ADR-010-D1-Access-Port/IMP-ADR010-01-*.md` |
| CLAUDE.md → archived IMP | `docs/design/_archive/ADR-007-Monorepo-Restructure/IMP-ADR007-01-*.md` |

## Consequences

### Positive

- Opening one folder shows the decision and all its execution plans together
- ADR↔IMP cross-references become filename-only (simpler, never break on archive)
- Archiving is a single folder move; no per-file reference fixes needed inside the folder
- Directory listing immediately shows active vs archived ADRs

### Negative

- Cross-ADR references become longer (`../ADR-012-Foo/IMP-ADR012-01-*.md` vs `IMP-ADR012-01-*.md`)
- One-time migration effort: ~77 files to move, ~65+ files with cross-reference updates
- ADRs without IMPs still get their own folder (minor overhead for uniformity)

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR016-01](IMP-ADR016-01-design-docs-folder-restructure.md) | Python migration script, all file moves, cross-reference updates, ADR templates, CLAUDE.md updates | — |

## Status Log

- 2026-05-22: Proposed and accepted during brainstorming session
- 2026-05-24: Completed after final documentation cleanup, stale-link verification, and whole-folder archival.
