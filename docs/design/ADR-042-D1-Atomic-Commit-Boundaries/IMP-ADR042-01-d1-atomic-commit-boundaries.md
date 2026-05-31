# IMP-ADR042-01: D1 Atomic-Commit Boundaries Docs Follow-through - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-042](ADR-042-d1-atomic-commit-boundaries.md) - this is **Phase 1**.

**Status:** Completed — 2026-06-01 (all tasks executed and verified; boundary vocabulary landed in `CONTEXT.md`, the storage READMEs, and the developer / ops handbooks).

**Goal:** Propagate the ADR-042 D1 write boundary into the canonical vocabulary and the docs that explain history writes, rollback, and recovery, so future D1 write changes are classified before merge.

**Architecture:** This is a documentation-only follow-through, not a runtime refactor. The source of truth remains ADR-042. `CONTEXT.md` holds the **canonical definitions** of the three write classes and the atomic-commit term; every other doc states the rule in one or two sentences and **links** to ADR-042 / CONTEXT.md rather than re-defining all three classes verbatim (the repo's anti-pattern rules forbid cross-doc copy-paste). The terms are anchored in `CONTEXT.md` first, then referenced from the storage module READMEs and the developer / operator handbook pairs.

**Tech Stack:** Markdown, `rg`, `git diff --check`, existing `docs/handbook/en` / `docs/handbook/zh` pairs.

**Terminology note:** Use **"session-level atomic commit"** (会话级原子提交) — never "logical ACID". ADR-042 deliberately scopes the guarantee to atomicity + consistency (isolation via `SessionId`/`MovieClaim`, durability via recovery). The word "ACID" only appears when *rejecting* a full distributed transaction.

---

## File Structure

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `CONTEXT.md` | Modify | **Canonical** home of the boundary vocabulary: add a bilingual `写入边界分类（Write Boundary Classes）` subsection under `## 存储层（Storage Layer）` and four rows to the trilingual glossary table at the bottom. |
| `javdb/storage/README.md` | Modify | One-paragraph guardrail that names the three classes and links to ADR-042 / CONTEXT.md — does not re-define them. |
| `javdb/storage/db/README.md` | Modify | One-line guardrail next to the low-level history / reports / rollback modules, linking to the same source. |
| `docs/handbook/en/developer/history-system.md` | Modify | Add a `D1 Write Boundary` subsection: `MovieHistory` / `TorrentHistory` are authoritative session-scoped writes; additive / diagnostic D1 records stay outside the commit boundary. Link to ADR-042. |
| `docs/handbook/zh/developer/history-system.md` | Modify | Mirror the English boundary subsection in Chinese. |
| `docs/handbook/en/ops/d1-rollback.md` | Modify | The `D1 Recovery Outbox` section already states "diagnostic only: the write still fails" — do **not** duplicate it. Add only an ADR-042 cross-reference and the "gates but never upgrades" nuance. |
| `docs/handbook/zh/ops/d1-rollback.md` | Modify | Mirror the English cross-reference + nuance in Chinese. |

> **Note:** ADR-042 (`.md` + `.zh.md`) is intentionally **not** in this table. Its `Related Implementation Plans` field and `Implementation Roadmap` row already link to this IMP — there is no placeholder left to replace (the old "No separate IMP" text no longer exists). See the dropped task note below.

## Task 1: Teach the canonical vocabulary the D1 boundary

**Files:**
- Modify: `CONTEXT.md`

- [x] **Step 1: Add a bilingual write-boundary subsection under `## 存储层（Storage Layer）`.**

Match CONTEXT.md's house style: bilingual `中文（English）` heading, Chinese prose. Insert after the `### Drift（漂移）` subsection:

```md
### 写入边界分类（Write Boundary Classes）

仓库现在把 D1 写入区分为三类（详见 [ADR-042](docs/design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)）：

- **权威写入（Authoritative write）** —— 决定一个 session 是否正确提交的写入。
- **增量写入（Additive write）** —— 可重放、可重建的状态，不决定 session 成败。
- **诊断写入（Diagnostic write）** —— 解释 drift / 恢复状态的可观测性数据，永不改变正确性（可以**阻塞**提交，但永不**升级**失败为成功）。
- **会话级原子提交（Session-level atomic commit）** —— 权威写入在 session 范围内表现为一个全有全无整体（原子性 + 一致性）的保证；隔离性靠 `SessionId` / `MovieClaim`、持久性靠 recovery，不在此边界内。
```

- [x] **Step 2: Add four rows to the trilingual glossary table at the bottom of `CONTEXT.md`.**

The bottom glossary uses `| 中文 | English | 中文说明 |`. Append:

```md
| 权威写入 | Authoritative write | 决定 session 是否正确提交的写入（ADR-042） |
| 增量写入 | Additive write | 可重放 / 可重建、不决定 session 成败的写入（ADR-042） |
| 诊断写入 | Diagnostic write | 解释 drift / 恢复状态、永不改变正确性的写入；可阻塞但不升级提交（ADR-042） |
| 会话级原子提交 | Session-level atomic commit | 权威写入在 session 范围内全有全无（A+C）；I 靠 SessionId/MovieClaim、D 靠 recovery（ADR-042） |
```

- [x] **Step 3: Verify the vocabulary is present in both the subsection and the glossary.**

Run:

```bash
rg -n -e "写入边界分类" -e "Session-level atomic commit" -e "会话级原子提交" CONTEXT.md
rg -ni "logical acid|逻辑 ACID" CONTEXT.md
```

Expected: the subsection heading is present; `Session-level atomic commit` / `会话级原子提交` each appear **at least twice** (once in the subsection, once in the glossary — this is expected, not a duplicate defect). The second grep returns **no hits** (the rejected term must never enter CONTEXT.md).

## Task 2: Mark the storage docs with a link-back guardrail

**Files:**
- Modify: `javdb/storage/README.md`
- Modify: `javdb/storage/db/README.md`

- [x] **Step 1: Add a brief guardrail to the storage overview that links rather than copies.**

Use a short paragraph like:

```md
New D1-backed writes are not one undifferentiated category. Any new write path must declare itself **authoritative**, **additive**, or **diagnostic** before it lands — authoritative writes are session-scoped and fail closed, additive writes are replayable, diagnostic writes explain drift / recovery and never decide session truth. See [ADR-042](../../docs/design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) and the `写入边界分类` section of `CONTEXT.md` for the canonical definitions.
```

(Confirm the relative path to `docs/design/...` resolves from `javdb/storage/README.md`; adjust `../` depth if needed.)

- [x] **Step 2: Add a one-line pointer next to the low-level db modules.**

In `javdb/storage/db/README.md`, near `db_history_write.py`, `db_reports.py`, and `db_rollback.py`, add a single sentence: "`MovieHistory` / `TorrentHistory` writes here are **authoritative** (session-level atomic commit); see ADR-042 for the additive / diagnostic classes." Do not restate all three definitions — link instead.

- [x] **Step 3: Verify the guardrail and link are present.**

Run:

```bash
rg -n -e "authoritative" -e "additive" -e "diagnostic" -e "ADR-042" javdb/storage/README.md javdb/storage/db/README.md
```

Expected: each README names the classification and links to ADR-042.

## Task 3: Mirror the boundary in developer and operator docs

**Files:**
- Modify: `docs/handbook/en/developer/history-system.md`
- Modify: `docs/handbook/zh/developer/history-system.md`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [x] **Step 1: Add a `D1 Write Boundary` subsection to the history-system guide (after `### Storage`).**

```md
### D1 Write Boundary

`MovieHistory` and `TorrentHistory` are **authoritative** session-scoped writes: they stage into pending tables and only become true at commit time (session-level atomic commit). Additive D1 tables may be replayable or rebuildable, but they must not decide whether a session commits. Diagnostic records such as drift logs or recovery outbox entries are observability aids, not user truth. See [ADR-042](../../../design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) for the classification rule.
```

Mirror the same structure and meaning in `docs/handbook/zh/developer/history-system.md` (Chinese prose, link to the `.zh.md` ADR). Confirm the relative `../../../design/...` depth resolves from `docs/handbook/{en,zh}/developer/`.

- [x] **Step 2: In the rollback handbook, cross-reference ADR-042 — do NOT duplicate the diagnostic-only sentence.**

The `D1 Recovery Outbox` section **already** states "queued outbox work is diagnostic only: the write still fails" and that an undrained ordering key / dead-lettered entry blocks the commit. Add only one short sentence that links the existing behavior to the design decision:

```md
This diagnostic-only-but-commit-gating behavior is the operator-facing form of ADR-042's write-class boundary: a recovery record can **block** an authoritative commit, but it never **upgrades** a failed write into a success. See [ADR-042](../../../design/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md).
```

Mirror the same sentence (and link to the `.zh.md` ADR) in `docs/handbook/zh/ops/d1-rollback.md`.

- [x] **Step 3: Verify the boundary text and ADR link are present, with no duplicated definition.**

Run:

```bash
rg -n -e "D1 Write Boundary" -e "ADR-042" docs/handbook/en/developer/history-system.md docs/handbook/zh/developer/history-system.md docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
rg -c "diagnostic only" docs/handbook/en/ops/d1-rollback.md
```

Expected: the new subsection and the ADR-042 cross-reference appear in each language pair; `diagnostic only` still appears **once** in the rollback handbook (we linked, not copied).

## Task 4: Final validation

- [x] **Step 1: Check markdown hygiene.**

```bash
git diff --check
```

Expected: no whitespace or patch-format errors.

- [x] **Step 2: Confirm the touched docs are the only intended changes.**

```bash
git status --short
```

Expected: only `CONTEXT.md`, the two storage READMEs, and the four handbook files are listed as modified (the ADR-042 rename + content edits are a separate, already-committed change).

## Dropped task (was Task 4 "Link the ADR to the plan")

The original plan included a task to "replace the placeholder roadmap text with the concrete IMP link." That work is **already done**: both `ADR-042-d1-atomic-commit-boundaries.md` and its `.zh.md` already carry the `[IMP-ADR042-01]` link in the `Related Implementation Plans` field and in the `Implementation Roadmap` row, and the old "No separate IMP" placeholder no longer exists. No edit is required; the task is removed to avoid describing completed work as pending.

## Rollback

Revert the docs-only commit. No schema, code, or workflow behavior changes are expected in this phase.

## Out of Scope

- Runtime changes to `javdb/storage/*` write helpers.
- New D1 migrations or schema edits.
- Any attempt to simulate distributed ACID across SQLite and D1.
- **Enforcing D6 via `ADR-TEMPLATE.md`** — adding a "declare the D1 write class" field/checkbox to the ADR template (so the classification is asked at design-review time) is deferred pending a decision on how strongly D6 should bite. Until then D6 lives as documented guidance, not a template gate.
