# docs/design/ Folder Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related**: implements [ADR-016](ADR-016-design-docs-folder-restructure.md)
**Source spec:** [ADR-016](ADR-016-design-docs-folder-restructure.md), D1-D7.

**Goal:** Restructure `docs/design/` so each ADR and BFR lives in its own Pascal-Kebab folder, with IMPs co-located inside their parent ADR folder. Eliminate the parallel `adr/`, `impl/`, `bfr/` directories.

**Architecture:** A Python migration script handles directory creation, file moves (via `git mv`), and cross-reference updates in ~77 markdown files. CLAUDE.md receives manual structural edits (tree diagrams, tables, prose rules) beyond what link-path changes cover. New ADR templates are created in `_templates/`.

**Tech Stack:** Python 3, git, grep

---

## File Structure

**Created (temporary):**
- `_restructure_design.py` — Migration script (deleted after use)

**Created (permanent):**
- `docs/design/_templates/ADR-TEMPLATE.md`
- `docs/design/_templates/ADR-TEMPLATE.zh.md`

**Moved (77 files):**
- 22 ADR files from `adr/` → `ADR-NNN-Foo/` or `_archive/ADR-NNN-Foo/` (includes ADR-016)
- 11 ADR files from `adr/archive/` → `_archive/ADR-NNN-Foo/`
- 32 IMP files from `impl/` → parent `ADR-NNN-Foo/` or `_archive/ADR-NNN-Foo/` (includes this IMP)
- 8 IMP files from `impl/archive/` → parent `ADR-NNN-Foo/` or `_archive/ADR-NNN-Foo/`
- 2 BFR files from `bfr/` → `BFR-001-Login-Proxy-Mismatch/`
- 2 BFR templates from `bfr/` → `_templates/` (renamed `BFR-TEMPLATE.*`)

**Modified (cross-reference updates):**
- All 77 moved files
- `docs/design/architecture/*.md` (3 files reference archived ADRs)
- `CLAUDE.md` (structural + link changes)
- `README.md`, `README_CN.md`, `apps/cli/README.md`
- `apps/cli/*/README.md`, `javdb/legacy/README.md`, `scripts/README.md` (scanned)

**Deleted (empty directories):**
- `docs/design/adr/archive/`, `docs/design/adr/`
- `docs/design/impl/archive/`, `docs/design/impl/`
- `docs/design/bfr/`

---

### Task 1: Write the migration script

**Files:**
- Create: `_restructure_design.py`

- [ ] **Step 1: Create the migration script**

Save to `_restructure_design.py` at the repository root:

```python
#!/usr/bin/env python3
"""docs/design/ folder restructure migration.

Moves ADR, IMP, BFR files into per-ADR folders and updates all
markdown cross-references. Run from the repository root.

Usage:
    python3 _restructure_design.py --dry-run   # Preview changes
    python3 _restructure_design.py              # Execute
"""

import os
import re
import subprocess
import sys
from pathlib import Path

DRY_RUN = "--dry-run" in sys.argv
ROOT = Path.cwd()
DESIGN = ROOT / "docs" / "design"

# ADR number → (folder_name, archived)
ADR_FOLDERS = {
    "001": ("ADR-001-Split-Db-Module", True),
    "002": ("ADR-002-Observability-Storage", True),
    "003": ("ADR-003-Metrics-Pipeline", True),
    "004": ("ADR-004-Proxy-Discovery", True),
    "005": ("ADR-005-Db-Py-Retirement", True),
    "006": ("ADR-006-Pending-Mode-Rollout", True),
    "007": ("ADR-007-Monorepo-Restructure", True),
    "008": ("ADR-008-Frontend-Rewrite", False),
    "009": ("ADR-009-D1-Drift-Classifier", False),
    "010": ("ADR-010-D1-Access-Port", False),
    "011": ("ADR-011-Parsing-Module", False),
    "012": ("ADR-012-Pipeline-Run-Boundary", False),
    "013": ("ADR-013-Runner-Runtime-State", False),
    "014": ("ADR-014-Storage-Cli-Layering", False),
    "015": ("ADR-015-Integrations-Interface", False),
    "016": ("ADR-016-Design-Docs-Restructure", False),
}

BFR_FOLDERS = {
    "001": ("BFR-001-Login-Proxy-Mismatch", False),
}


def adr_num(filename):
    """Extract ADR number from ADR-NNN or IMP-ADRNNN filename."""
    m = re.match(r"(?:ADR|IMP-ADR)(\d{3})", filename)
    return m.group(1) if m else None


def target_dir(number, archived):
    folder = ADR_FOLDERS[number][0]
    return Path("_archive") / folder if archived else Path(folder)


def build_mapping():
    """Build {old_path: new_path} mapping, all relative to ROOT."""
    mapping = {}

    # ADR files in adr/ (some active, some → _archive)
    for f in sorted((DESIGN / "adr").glob("*.md")):
        num = adr_num(f.name)
        if num and num in ADR_FOLDERS:
            _, arch = ADR_FOLDERS[num]
            dest = DESIGN / target_dir(num, arch) / f.name
            mapping[str(f.relative_to(ROOT))] = str(dest.relative_to(ROOT))

    # ADR files in adr/archive/ → all go to _archive/
    archive_dir = DESIGN / "adr" / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.md")):
            num = adr_num(f.name)
            if num and num in ADR_FOLDERS:
                dest = DESIGN / "_archive" / ADR_FOLDERS[num][0] / f.name
                mapping[str(f.relative_to(ROOT))] = str(dest.relative_to(ROOT))

    # IMP files in impl/ → parent ADR folder
    for f in sorted((DESIGN / "impl").glob("*.md")):
        num = adr_num(f.name)
        if num and num in ADR_FOLDERS:
            _, arch = ADR_FOLDERS[num]
            dest = DESIGN / target_dir(num, arch) / f.name
            mapping[str(f.relative_to(ROOT))] = str(dest.relative_to(ROOT))

    # IMP files in impl/archive/ → parent ADR folder
    # (completed IMP + active ADR → stays with ADR, not doubly archived)
    impl_archive = DESIGN / "impl" / "archive"
    if impl_archive.exists():
        for f in sorted(impl_archive.glob("*.md")):
            num = adr_num(f.name)
            if num and num in ADR_FOLDERS:
                _, arch = ADR_FOLDERS[num]
                dest = DESIGN / target_dir(num, arch) / f.name
                mapping[str(f.relative_to(ROOT))] = str(dest.relative_to(ROOT))

    # BFR files
    bfr_dir = DESIGN / "bfr"
    if bfr_dir.exists():
        for f in sorted(bfr_dir.glob("*.md")):
            if f.name.startswith("BFR-"):
                m = re.match(r"BFR-(\d{3})", f.name)
                if m and m.group(1) in BFR_FOLDERS:
                    folder = BFR_FOLDERS[m.group(1)][0]
                    dest = DESIGN / folder / f.name
                    mapping[str(f.relative_to(ROOT))] = str(dest.relative_to(ROOT))
            elif f.name == "_TEMPLATE.md":
                mapping[str(f.relative_to(ROOT))] = str(
                    (DESIGN / "_templates" / "BFR-TEMPLATE.md").relative_to(ROOT)
                )
            elif f.name == "_TEMPLATE.zh.md":
                mapping[str(f.relative_to(ROOT))] = str(
                    (DESIGN / "_templates" / "BFR-TEMPLATE.zh.md").relative_to(ROOT)
                )

    return mapping


def files_to_scan(mapping):
    """All files whose markdown links need scanning."""
    files = set(mapping.keys())

    # Architecture docs
    arch_dir = DESIGN / "architecture"
    if arch_dir.exists():
        for f in arch_dir.glob("*.md"):
            files.add(str(f.relative_to(ROOT)))

    # External files with known references
    for ext in [
        "CLAUDE.md", "README.md", "README_CN.md",
        "apps/cli/README.md",
        "javdb/legacy/README.md", "scripts/README.md",
    ]:
        if (ROOT / ext).exists():
            files.add(ext)

    # CLI sub-READMEs
    for f in ROOT.glob("apps/cli/*/README.md"):
        files.add(str(f.relative_to(ROOT)))

    # Spec files
    specs = ROOT / "docs" / "superpowers" / "specs"
    if specs.exists():
        for f in specs.glob("*.md"):
            files.add(str(f.relative_to(ROOT)))

    return sorted(files)


def update_links(content, old_path, new_path, mapping):
    """Update markdown links based on old→new file positions."""
    old_dir = os.path.dirname(old_path)
    new_dir = os.path.dirname(new_path)

    def replacer(m):
        text, url = m.group(1), m.group(2)
        if url.startswith(("http", "#", "mailto:")):
            return m.group(0)

        frag = ""
        if "#" in url:
            url, frag = url.split("#", 1)
            frag = "#" + frag

        if not url:
            return m.group(0)

        # Resolve link against file's OLD directory
        resolved = os.path.normpath(os.path.join(old_dir, url))

        # Case 1: target moved — recompute from file's NEW directory
        if resolved in mapping:
            new_rel = os.path.relpath(mapping[resolved], new_dir)
            return f"[{text}]({new_rel}{frag})"

        # Case 2: target didn't move but source did — recompute relative path
        if old_dir != new_dir and (ROOT / resolved).exists():
            new_rel = os.path.relpath(resolved, new_dir)
            if new_rel != url:
                return f"[{text}]({new_rel}{frag})"

        return m.group(0)

    return re.sub(r'\[([^\]]*)\]\(([^)]+)\)', replacer, content)


def main():
    mapping = build_mapping()
    print(f"Files to move: {len(mapping)}")
    for old, new in sorted(mapping.items()):
        print(f"  {old}")
        print(f"    → {new}")

    if DRY_RUN:
        print("\n[DRY RUN] No changes made.")
        return

    # Phase 1: Create target directories
    dirs_needed = sorted(set(os.path.dirname(p) for p in mapping.values()))
    for d in dirs_needed:
        os.makedirs(d, exist_ok=True)
        print(f"mkdir: {d}")

    # Phase 2: Update cross-references in all affected files
    scan = files_to_scan(mapping)
    for fpath in scan:
        full = ROOT / fpath
        if not full.exists():
            continue
        content = full.read_text()
        new_path = mapping.get(fpath, fpath)
        updated = update_links(content, fpath, new_path, mapping)
        if updated != content:
            full.write_text(updated)
            print(f"Updated links: {fpath}")

    # Phase 3: Move files via git mv
    for old, new in sorted(mapping.items()):
        subprocess.run(["git", "mv", old, new], check=True)
        print(f"Moved: {old} → {new}")

    # Phase 4: Remove empty directories
    for d in [
        "docs/design/impl/archive", "docs/design/impl",
        "docs/design/adr/archive", "docs/design/adr",
        "docs/design/bfr",
    ]:
        dp = ROOT / d
        if dp.exists() and not any(dp.iterdir()):
            dp.rmdir()
            print(f"Removed empty: {d}")

    print(f"\nDone! {len(mapping)} files moved.")
    print("Review with: git diff --stat && git diff")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script compiles**

Run: `python3 -c "import py_compile; py_compile.compile('_restructure_design.py', doraise=True)"`
Expected: No output (success).

- [ ] **Step 3: Commit the script**

```bash
git add _restructure_design.py
git commit -m "chore: add docs/design/ restructure migration script"
```

---

### Task 2: Dry-run and review

- [ ] **Step 1: Run the script in dry-run mode**

Run: `python3 _restructure_design.py --dry-run`
Expected: List of ~77 file move mappings. No files changed on disk.

- [ ] **Step 2: Verify key classifications**

Check the dry-run output for:
- **Active ADR folders**: ADR-008, 009, 010, 011, 012, 013, 014, 015, 016
- **Archived ADR folders**: ADR-001, 002, 003, 004, 005, 006, 007
- **IMP-ADR008-01** goes to `ADR-008-Frontend-Rewrite/` (completed IMP, active ADR → NOT archived)
- **IMP-ADR003-01, IMP-ADR003-02** go to `_archive/ADR-003-Metrics-Pipeline/`
- **ADR-005, ADR-006** go to `_archive/` (both are completed)
- **ADR-016** + **IMP-ADR016-01** go to `ADR-016-Design-Docs-Restructure/`
- **BFR templates** rename to `BFR-TEMPLATE.md` / `BFR-TEMPLATE.zh.md`

---

### Task 3: Execute migration and verify

- [ ] **Step 1: Execute the migration**

Run: `python3 _restructure_design.py`

- [ ] **Step 2: Verify git status**

Run: `git status`
Expected: ~77 renames. Old `adr/`, `impl/`, `bfr/` directories removed. No untracked files.

- [ ] **Step 3: Spot-check cross-references**

Run each command and verify the output matches the expected pattern:

```bash
# ADR → own IMP: should be filename-only (no ../ prefix)
grep -n 'impl/' docs/design/ADR-014-Storage-Cli-Layering/ADR-014-storage-cli-layering.md
# Expected: NO matches (all ../impl/ paths replaced with filename-only)

# IMP → own ADR: should be filename-only
grep 'Source spec.*ADR-012' docs/design/ADR-012-Pipeline-Run-Boundary/IMP-ADR012-01-pipeline-run-phase1-result-sidecar.md
# Expected: [ADR-012](ADR-012-pipeline-run-structured-boundary.md)

# Cross-ADR reference: should use ../ADR-NNN-Foo/ pattern
grep 'ADR-014' docs/design/ADR-008-Frontend-Rewrite/IMP-ADR008-02-frontend-phase1-completion.md | head -3
# Expected: ../ADR-014-Storage-Cli-Layering/ADR-014-... or ../ADR-014-Storage-Cli-Layering/IMP-ADR014-...

# Archived IMP → own archived ADR: should be filename-only (same folder)
grep 'ADR-007.*monorepo' docs/design/_archive/ADR-007-Monorepo-Restructure/IMP-ADR007-02-restructure-phase2-scripts-to-cli.md | head -3
# Expected: [ADR-007](ADR-007-monorepo-restructure-2026-05.md)

# Architecture doc → archived ADR: should use ../_archive/ pattern
grep 'ADR-007' docs/design/architecture/python-tree-2026-05.md
# Expected: ../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md

# External: CLAUDE.md ADR-007 link
grep 'ADR-007.*monorepo' CLAUDE.md
# Expected: docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md

# ADR-016 → own IMP: should be filename-only (same folder)
grep 'IMP-ADR016' docs/design/ADR-016-Design-Docs-Restructure/ADR-016-design-docs-folder-restructure.md
# Expected: [IMP-ADR016-01](IMP-ADR016-01-design-docs-folder-restructure.md)
```

- [ ] **Step 4: Commit the migration**

```bash
git add -A
git commit -m "refactor(docs): restructure design/ into per-ADR folders with co-located IMPs"
```

---

### Task 4: Create ADR templates

**Files:**
- Create: `docs/design/_templates/ADR-TEMPLATE.md`
- Create: `docs/design/_templates/ADR-TEMPLATE.zh.md`

- [ ] **Step 1: Create the English ADR template**

Write to `docs/design/_templates/ADR-TEMPLATE.md`:

```markdown
# ADR-NNN: Title

**Status:** Proposed | Accepted | Completed | Superseded
**Date:** YYYY-MM-DD
**Author:**
**Related Implementation Plans:** [IMP-ADRNNN-01](IMP-ADRNNN-01-topic.md) (Phase 1 — description)

## Context

What problem or opportunity motivates this decision?

## Decision

What is the change being made?

### Design Decisions

D1. **Decision title** — Description and rationale.

## Consequences

### Positive

- Benefit 1

### Negative

- Trade-off 1

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADRNNN-01](IMP-ADRNNN-01-topic.md) | What ships | What's deferred |

## References

- [Related ADR](../ADR-NNN-Foo/ADR-NNN-topic.md)

## Status Log

- YYYY-MM-DD: Proposed
```

- [ ] **Step 2: Create the Chinese ADR template**

Write to `docs/design/_templates/ADR-TEMPLATE.zh.md`:

```markdown
# ADR-NNN: 标题

**状态 (Status):** Proposed | Accepted | Completed | Superseded
**日期 (Date):** YYYY-MM-DD
**作者 (Author):**
**关联实现计划 (Related Implementation Plans):** [IMP-ADRNNN-01](IMP-ADRNNN-01-topic.md)（Phase 1 — 描述）

## 背景 (Context)

是什么问题或机会促使了这个决策？

## 决策 (Decision)

正在做出什么变更？

### 设计决策 (Design Decisions)

D1. **决策标题** — 描述和理由。

## 后果 (Consequences)

### 正面 (Positive)

- 好处 1

### 负面 (Negative)

- 权衡 1

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADRNNN-01](IMP-ADRNNN-01-topic.md) | 交付内容 | 推迟内容 |

## 参考 (References)

- [相关 ADR](../ADR-NNN-Foo/ADR-NNN-topic.md)

## 状态日志 (Status Log)

- YYYY-MM-DD: Proposed
```

- [ ] **Step 3: Review BFR templates for stale path patterns**

Run:
```bash
grep -n '\.\./adr/\|\.\.\/impl/\|\.\.\/bfr/' docs/design/_templates/BFR-TEMPLATE*.md
```

Expected: The BFR templates contain `../adr/ADR-NNN-xxx.md` as placeholder text. Update these to the new pattern:

In `docs/design/_templates/BFR-TEMPLATE.md`, replace:
```
**Related**: [ADR-NNN](../adr/ADR-NNN-xxx.md), [PR #NN](https://github.com/...)
```
with:
```
**Related**: [ADR-NNN](../ADR-NNN-Foo/ADR-NNN-xxx.md), [PR #NN](https://github.com/...)
```

Apply the same change in `BFR-TEMPLATE.zh.md`.

- [ ] **Step 4: Commit templates**

```bash
git add docs/design/_templates/ADR-TEMPLATE.md docs/design/_templates/ADR-TEMPLATE.zh.md docs/design/_templates/BFR-TEMPLATE.md docs/design/_templates/BFR-TEMPLATE.zh.md
git commit -m "docs: add ADR templates and fix BFR template paths in _templates/"
```

---

### Task 5: Update CLAUDE.md structural content

**Files:**
- Modify: `CLAUDE.md:572-574,600,614-617,625,630-632,639-643,665-666,669`

The migration script already updated markdown link URLs (e.g. `[ADR-007](docs/design/adr/archive/...)` → `[ADR-007](docs/design/_archive/ADR-007-Monorepo-Restructure/...)`). This task handles structural content: tree diagrams, tables, prose rules, and directory link targets that the script couldn't update.

- [ ] **Step 1: Update the Related Documentation section**

Replace (lines 572-574):
```markdown
- [docs/design/adr/](docs/design/adr/) — Architecture decision records (design documents, bilingual `.md` + `.zh.md`)
- [docs/design/impl/](docs/design/impl/) — Implementation plans (step-by-step execution checklists, English only)
- [docs/design/bfr/](docs/design/bfr/) — Bug fix records (design flaw / logic bug retrospectives, bilingual `.md` + `.zh.md`)
```

With:
```markdown
- [docs/design/](docs/design/) — Design records: each ADR and BFR has its own folder (e.g. `ADR-010-D1-Access-Port/`), with IMPs co-located inside. Completed ADRs/BFRs in `_archive/`. Templates in `_templates/`.
```

- [ ] **Step 2: Update the Audience-First Layout tree**

Replace (line 600):
```
├── design/                    Design records (architecture/, adr/, impl/, bfr/)
```

With:
```
├── design/                    Design records (ADR-NNN-Foo/, _archive/, _templates/, architecture/)
```

- [ ] **Step 3: Update the Rules section**

Replace (lines 614-617):
```markdown
- `docs/design/` holds design records — ADRs, IMPs, BFRs, and architecture analyses. **ADRs and BFRs are bilingual; IMP files are English only** — see "Design Docs vs Implementation Plans vs Bug Fix Records" below
- Architecture analyses go in `docs/design/architecture/`
- Cross-cutting decisions (the WHY/WHAT) go in `docs/design/adr/` as `ADR-NNN-<topic>.md` + paired `.zh.md`
- Step-by-step execution checklists (the HOW) go in `docs/design/impl/` as `IMP-NNN-<topic>.md` (English only)
```

With:
```markdown
- `docs/design/` holds design records — each ADR/BFR in its own folder (e.g. `ADR-010-D1-Access-Port/`), with IMPs co-located inside. **ADRs and BFRs are bilingual; IMP files are English only** — see "Design Docs vs Implementation Plans vs Bug Fix Records" below
- Architecture analyses go in `docs/design/architecture/`
- Cross-cutting decisions (the WHY/WHAT) go in `docs/design/ADR-NNN-Foo/ADR-NNN-<topic>.md` + paired `.zh.md`
- Step-by-step execution checklists (the HOW) go in `docs/design/ADR-NNN-Foo/IMP-ADRNNN-PP-<topic>.md` (English only, co-located with parent ADR)
```

- [ ] **Step 4: Update the Design Docs table — Location row**

Replace (line 625):
```
| Location | `docs/design/adr/ADR-NNN-<topic>.md` | `docs/design/impl/IMP-ADRNNN-PP-<topic>.md` | `docs/design/bfr/BFR-NNN-<topic>.md` |
```

With:
```
| Location | `docs/design/ADR-NNN-Foo/ADR-NNN-<topic>.md` | `docs/design/ADR-NNN-Foo/IMP-ADRNNN-PP-<topic>.md` | `docs/design/BFR-NNN-Foo/BFR-NNN-<topic>.md` |
```

- [ ] **Step 5: Update the Cross-link rows**

Replace (lines 630-631):
```
| Cross-link from sibling | `[ADR-007](ADR-007-foo.md)` | `[IMP-ADR007-02](IMP-ADR007-02-bar.md)` | `[BFR-003](BFR-003-foo.md)` |
| Cross-link to other types | `[IMP-ADR007-02](../impl/IMP-ADR007-02-bar.md)` | `[ADR-007](../adr/ADR-007-foo.md)` | `[ADR-009](../adr/ADR-009-foo.md)` |
```

With:
```
| Cross-link (same folder) | `[IMP-ADR007-02](IMP-ADR007-02-bar.md)` (filename only) | `[ADR-007](ADR-007-foo.md)` (filename only) | `[BFR-003](BFR-003-foo.md)` (filename only) |
| Cross-link (other folder) | `[ADR-012](../ADR-012-Foo/ADR-012-bar.md)` | `[IMP-ADR012-01](../ADR-012-Foo/IMP-ADR012-01-bar.md)` | `[ADR-009](../ADR-009-Foo/ADR-009-bar.md)` |
```

- [ ] **Step 6: Update the Archival row**

Replace (line 632):
```
| Archival | Completed ADRs → `docs/design/adr/archive/` | Completed IMPs → `docs/design/impl/archive/` | (no archival convention yet) |
```

With:
```
| Archival | Whole folder → `docs/design/_archive/ADR-NNN-Foo/` (ADR completed + all IMPs done) | Stays with parent ADR; archives when ADR archives | Whole folder → `docs/design/_archive/BFR-NNN-Foo/` |
```

- [ ] **Step 7: Replace the IMP archival paragraph**

Replace (lines 639-643):
```markdown
**IMP archival:** When all tasks in an IMP are completed, move the file to `docs/design/impl/archive/`. When archiving:

1. Update all inbound references to point to `archive/` (e.g., `../impl/X.md` → `../impl/archive/X.md`)
2. Update all relative paths within the archived file itself (e.g., `../adr/` → `../../adr/` since the file is now one level deeper)
3. Update inter-IMP references: archived→archived stays as filename-only; archived→active uses `../`
```

With:
```markdown
**Whole-folder archival:** When an ADR's status is Completed or Superseded AND all its IMPs are complete, move the entire `ADR-NNN-Foo/` folder into `_archive/ADR-NNN-Foo/`. Internal references (ADR↔IMP within the folder) use filename-only links and need no changes. Update external references that point into the moved folder by inserting `_archive/` into the path. Same pattern applies to BFR folders.

**Completed IMPs in active ADRs:** Completed IMPs stay in their parent ADR folder. Completion is tracked by the `Status:` field inside the file, not by directory location.
```

- [ ] **Step 8: Update the skill output routing table**

Replace (lines 665-666):
```
| `superpowers:brainstorming` | `docs/superpowers/specs/` | `docs/design/adr/ADR-NNN-<topic>.md` (+ paired `.zh.md`) |
| `superpowers:writing-plans` | `docs/superpowers/plans/` | `docs/design/impl/IMP-ADRNNN-PP-<topic>.md` |
```

With:
```
| `superpowers:brainstorming` | `docs/superpowers/specs/` | `docs/design/ADR-NNN-Foo/ADR-NNN-<topic>.md` (+ paired `.zh.md`) |
| `superpowers:writing-plans` | `docs/superpowers/plans/` | `docs/design/ADR-NNN-Foo/IMP-ADRNNN-PP-<topic>.md` |
```

- [ ] **Step 9: Update the skill output routing rule**

Replace (line 669):
```
- `docs/superpowers/specs/` may hold in-progress drafts, but any finalized output **must** land in `docs/design/adr/` or `docs/design/impl/`.
```

With:
```
- `docs/superpowers/specs/` may hold in-progress drafts, but any finalized output **must** land in the appropriate `docs/design/ADR-NNN-Foo/` folder.
```

- [ ] **Step 10: Commit CLAUDE.md changes**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update CLAUDE.md for per-ADR folder structure"
```

---

### Task 6: Final verification and clean up

- [ ] **Step 1: Search for stale full-path references**

Run:
```bash
grep -rn 'docs/design/adr/\|docs/design/impl/\|docs/design/bfr/' --include='*.md' . | grep -v '.claude/worktrees' | grep -v '_restructure_design'
```

Expected: No matches. If any found, update them.

- [ ] **Step 2: Search for stale relative-path patterns in design docs**

Run:
```bash
grep -rn '\.\./adr/\|\.\.\/impl/\|\.\.\/bfr/' docs/design/ --include='*.md'
```

Expected: No matches. If any found, update them.

- [ ] **Step 3: Verify directory structure**

Run:
```bash
ls -d docs/design/*/
```

Expected output should show:
```
docs/design/ADR-008-Frontend-Rewrite/
docs/design/ADR-009-D1-Drift-Classifier/
docs/design/ADR-010-D1-Access-Port/
docs/design/ADR-011-Parsing-Module/
docs/design/ADR-012-Pipeline-Run-Boundary/
docs/design/ADR-013-Runner-Runtime-State/
docs/design/ADR-014-Storage-Cli-Layering/
docs/design/ADR-015-Integrations-Interface/
docs/design/ADR-016-Design-Docs-Restructure/
docs/design/BFR-001-Login-Proxy-Mismatch/
docs/design/_archive/
docs/design/_templates/
docs/design/architecture/
```

No `adr/`, `impl/`, or `bfr/` directories.

- [ ] **Step 4: Verify _archive contents**

Run:
```bash
ls docs/design/_archive/
```

Expected:
```
ADR-001-Split-Db-Module/
ADR-002-Observability-Storage/
ADR-003-Metrics-Pipeline/
ADR-004-Proxy-Discovery/
ADR-005-Db-Py-Retirement/
ADR-006-Pending-Mode-Rollout/
ADR-007-Monorepo-Restructure/
```

- [ ] **Step 5: Delete the migration script**

```bash
rm _restructure_design.py
git add _restructure_design.py
git commit -m "chore: remove migration script after docs/design/ restructure"
```
