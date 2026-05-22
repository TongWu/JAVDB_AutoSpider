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
    m = re.match(r"(?:ADR-|IMP-ADR)(\d{3})", filename)
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
