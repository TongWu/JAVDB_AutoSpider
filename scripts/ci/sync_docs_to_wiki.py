#!/usr/bin/env python3
"""Sync docs/en/ to the GitHub Wiki repository.

Reads wiki_mapping.json, copies each mapped file into the wiki checkout,
rewrites internal doc links to wiki-compatible [[Page-Name]] format,
and generates _Sidebar.md + Home.md navigation.

Usage:
    python scripts/ci/sync_docs_to_wiki.py --wiki-dir ../wiki --repo-root .
"""

import argparse
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path


def load_mapping(mapping_path: Path) -> list[dict]:
    with open(mapping_path) as f:
        data = json.load(f)
    return data["mappings"]


def build_link_rewrite_table(mappings: list[dict]) -> dict[str, str]:
    """Build a lookup from relative doc paths to wiki page names."""
    table = {}
    for m in mappings:
        source = m["source"]
        wiki_page = m["wiki_page"]
        table[source] = wiki_page
        filename = Path(source).name
        table[filename] = wiki_page
    return table


def rewrite_links(content: str, rewrite_table: dict[str, str]) -> str:
    """Replace markdown links pointing to docs/ files with wiki links."""

    def replace_match(match):
        text = match.group(1)
        target = match.group(2)
        clean = target.split("#")[0].split("?")[0]
        basename = Path(clean).name

        for doc_path, wiki_page in rewrite_table.items():
            if clean.endswith(doc_path) or basename == Path(doc_path).name:
                fragment = ""
                if "#" in target:
                    fragment = "#" + target.split("#", 1)[1]
                return f"[[{wiki_page}{fragment}|{text}]]"

        return match.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_match, content)


def generate_sidebar(mappings: list[dict]) -> str:
    categories = defaultdict(list)
    for m in mappings:
        categories[m["category"]].append(m["wiki_page"])

    lines = ["### Navigation", ""]
    lines.append("[[Home]]")
    lines.append("")

    for category, pages in categories.items():
        lines.append(f"**{category}**")
        lines.append("")
        for page in pages:
            display = page.replace("-", " ")
            lines.append(f"- [[{page}|{display}]]")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**Other**")
    lines.append("")
    lines.append("- [Chinese Docs (中文)](https://github.com/TongWu/JAVDB_AutoSpider_CICD/tree/main/docs/zh)")
    lines.append("- [CONTEXT.md](https://github.com/TongWu/JAVDB_AutoSpider_CICD/blob/main/CONTEXT.md)")
    lines.append("")
    return "\n".join(lines)


def generate_home(mappings: list[dict]) -> str:
    categories = defaultdict(list)
    for m in mappings:
        categories[m["category"]].append(m["wiki_page"])

    lines = [
        "# JavDB AutoSpider Wiki",
        "",
        "Welcome to the JavDB AutoSpider documentation wiki.",
        "",
        "> This wiki is auto-generated from [`docs/en/`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/tree/main/docs/en). "
        "Edit the source files there — changes sync on every push to `main`.",
        "",
        "For Chinese documentation, see [`docs/zh/`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/tree/main/docs/zh).",
        "",
    ]

    for category, pages in categories.items():
        lines.append(f"## {category}")
        lines.append("")
        for page in pages:
            display = page.replace("-", " ")
            lines.append(f"- [[{page}|{display}]]")
        lines.append("")

    return "\n".join(lines)


def sync(repo_root: Path, wiki_dir: Path, mapping_path: Path) -> None:
    mappings = load_mapping(mapping_path)
    rewrite_table = build_link_rewrite_table(mappings)

    synced = 0
    skipped = 0

    for m in mappings:
        source = repo_root / m["source"]
        dest = wiki_dir / f"{m['wiki_page']}.md"

        if not source.exists():
            print(f"  SKIP {m['source']} (not found)")
            skipped += 1
            continue

        content = source.read_text(encoding="utf-8")
        content = rewrite_links(content, rewrite_table)
        dest.write_text(content, encoding="utf-8")
        print(f"  SYNC {m['source']} → {m['wiki_page']}.md")
        synced += 1

    sidebar = generate_sidebar(mappings)
    (wiki_dir / "_Sidebar.md").write_text(sidebar, encoding="utf-8")
    print("  SYNC _Sidebar.md")

    home = generate_home(mappings)
    (wiki_dir / "Home.md").write_text(home, encoding="utf-8")
    print("  SYNC Home.md")

    print(f"\nDone: {synced} synced, {skipped} skipped")


def main():
    parser = argparse.ArgumentParser(description="Sync docs to wiki")
    parser.add_argument(
        "--wiki-dir",
        type=Path,
        required=True,
        help="Path to the wiki git checkout",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Path to the main repo root",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Path to wiki_mapping.json (default: scripts/ci/wiki_mapping.json)",
    )
    args = parser.parse_args()

    mapping_path = args.mapping or (args.repo_root / "scripts" / "ci" / "wiki_mapping.json")
    sync(args.repo_root, args.wiki_dir, mapping_path)


if __name__ == "__main__":
    main()
