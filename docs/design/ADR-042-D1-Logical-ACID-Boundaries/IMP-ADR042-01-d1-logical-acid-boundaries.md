# IMP-ADR042-01: D1 Logical ACID Boundaries Docs Follow-through - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-042](ADR-042-d1-logical-acid-boundaries.md) - this is **Phase 1**.

**Goal:** Propagate the ADR-042 D1 write boundary into the canonical vocabulary and the docs that explain history writes, rollback, and recovery so future D1 write changes are classified before merge.

**Architecture:** This is a documentation-only follow-through, not a runtime refactor. The source of truth remains ADR-042; the work here makes the boundary visible in the places engineers already use when they touch storage code or operate failed runs. We will anchor the terms in `CONTEXT.md`, then mirror the same boundary in the storage module READMEs and the developer/operator handbook pairs.

**Tech Stack:** Markdown, `rg`, `git diff --check`, existing `docs/handbook/en` / `docs/handbook/zh` pairs.

---

## File Structure

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `CONTEXT.md` | Modify | Add the D1 write-boundary vocabulary to the Storage Layer glossary and the terminology table. |
| `javdb/storage/README.md` | Modify | Add a short guardrail note that classifies D1-backed writes as authoritative, additive, or diagnostic. |
| `javdb/storage/db/README.md` | Modify | Document the same boundary next to the low-level history, reports, and rollback modules. |
| `docs/handbook/en/developer/history-system.md` | Modify | Explain that `MovieHistory` / `TorrentHistory` are authoritative session-scoped writes and that additive / diagnostic D1 records stay outside the commit boundary. |
| `docs/handbook/zh/developer/history-system.md` | Modify | Mirror the English boundary explanation in Chinese. |
| `docs/handbook/en/ops/d1-rollback.md` | Modify | Clarify that the recovery outbox is diagnostic only and does not turn a failed authoritative write into success. |
| `docs/handbook/zh/ops/d1-rollback.md` | Modify | Mirror the operator guidance in Chinese. |
| `docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.md` | Modify | Replace the placeholder roadmap text with the concrete IMP link. |
| `docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.zh.md` | Modify | Mirror the IMP link update in the Chinese ADR. |

## Task 1: Teach the canonical vocabulary the D1 boundary

**Files:**
- Modify: `CONTEXT.md`

- [ ] **Step 1: Add the write-boundary definitions where the storage-layer terms live.**

Insert a short storage-layer subsection that says the repository now distinguishes three D1 write classes:

```md
### Write Boundary Classes

- Authoritative write: decides whether a session committed correctly.
- Additive write: replayable or rebuildable state that does not decide session success.
- Diagnostic write: observability or recovery state that explains what happened but never changes correctness.
- Session-level logical ACID: the guarantee that authoritative writes behave like one atomic unit at session scope even though the system uses multiple layers underneath.
```

Also add the four entries to the glossary table at the bottom of `CONTEXT.md`.

- [ ] **Step 2: Verify the glossary is present and unique.**

Run:

```bash
rg -n -e "Authoritative write" -e "Additive write" -e "Diagnostic write" -e "Session-level logical ACID" CONTEXT.md
```

Expected: one hit for each term, no duplicates.

## Task 2: Mark the storage docs with the same guardrail

**Files:**
- Modify: `javdb/storage/README.md`
- Modify: `javdb/storage/db/README.md`

- [ ] **Step 1: Add a brief guardrail note to the storage overview.**

Use a short paragraph like:

```md
New D1-backed writes are not one undifferentiated category. Any new write path must declare itself as authoritative, additive, or diagnostic before it lands. Authoritative writes are session-scoped and fail closed; additive writes are replayable; diagnostic writes explain drift or recovery and never decide session truth.
```

- [ ] **Step 2: Repeat the same boundary next to the low-level db modules.**

Place a matching note near `db_history_write.py`, `db_reports.py`, and `db_rollback.py` in `javdb/storage/db/README.md` so readers see the rule where they inspect the actual write helpers.

- [ ] **Step 3: Verify the storage README wording is present.**

Run:

```bash
rg -n -e "authoritative" -e "additive" -e "diagnostic" javdb/storage/README.md javdb/storage/db/README.md
```

Expected: each README mentions the classification rule.

## Task 3: Mirror the boundary in developer and operator docs

**Files:**
- Modify: `docs/handbook/en/developer/history-system.md`
- Modify: `docs/handbook/zh/developer/history-system.md`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Add a dedicated write-boundary subsection to the history-system guide.**

Insert a new subsection after `Storage` that explains:

```md
### D1 Write Boundary

`MovieHistory` and `TorrentHistory` are authoritative session-scoped writes. They stage into pending tables and only become true at commit time. Additive D1 tables may be replayable or rebuildable, but they must not decide whether a session commits. Diagnostic records such as drift logs or recovery outbox entries are observability aids, not user truth.
```

Mirror the same structure and meaning in `docs/handbook/zh/developer/history-system.md`.

- [ ] **Step 2: Clarify the rollback handbook's outbox semantics.**

Add one short paragraph in the `D1 Recovery Outbox` section:

```md
The recovery outbox is diagnostic only. It can queue safe work for replay, but it never turns a failed authoritative write into a success.
```

Mirror the same point in `docs/handbook/zh/ops/d1-rollback.md`, and mention ADR-042 so readers can jump from ops guidance to the design decision.

- [ ] **Step 3: Verify the handbook files now mention the boundary.**

Run:

```bash
rg -n -e "D1 Write Boundary" -e "authoritative session-scoped" -e "diagnostic only" -e "ADR-042" docs/handbook/en/developer/history-system.md docs/handbook/zh/developer/history-system.md docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
```

Expected: the new subsection and outbox note are both present in each language pair.

## Task 4: Link the ADR to the plan

**Files:**
- Modify: `docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.md`
- Modify: `docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.zh.md`

- [ ] **Step 1: Replace the placeholder roadmap text with the concrete IMP link.**

Update the `Related Implementation Plans` field to:

```md
| **Related Implementation Plans** | [IMP-ADR042-01](IMP-ADR042-01-d1-logical-acid-boundaries.md) - Phase 1 docs follow-through |
```

Then update the implementation roadmap row to point at the same IMP and describe the docs propagation work rather than "No separate IMP".

- [ ] **Step 2: Verify the stale placeholder is gone.**

Run:

```bash
rg -n -e "No separate IMP" -e "IMP-ADR042-01" docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.md docs/design/ADR-042-D1-Logical-ACID-Boundaries/ADR-042-d1-logical-acid-boundaries.zh.md
```

Expected: the placeholder text is gone; the new link is present in both files.

## Task 5: Final validation

- [ ] **Step 1: Check markdown hygiene.**

Run:

```bash
git diff --check
```

Expected: no whitespace or patch-format errors.

- [ ] **Step 2: Confirm the touched docs are the only intended changes.**

Run:

```bash
git status --short
```

Expected: only the ADR-042 folder plus the docs files touched by this plan are listed.

## Rollback

Revert the docs-only commit. No schema, code, or workflow behavior changes are expected in this phase.

## Out of Scope

- Runtime changes to `javdb/storage/*` write helpers.
- New D1 migrations or schema edits.
- Any attempt to simulate distributed ACID across SQLite and D1.
