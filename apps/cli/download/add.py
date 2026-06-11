"""Demonstrator CLI: add a torrent via the active downloader backend (ADR-039 Phase 2).

Usage:
    python -m apps.cli.download.add --magnet <uri> --category <cat> [--name <name>]

Exits 0 on success, 1 on failure. Prints the DownloadResult summary.
This CLI exercises the full downloader-category stack end-to-end; it does NOT
replace apps.cli.qb.uploader (which has its own rich pipeline logic — see Out of Scope).
"""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.integrations.downloader import dispatch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a torrent via the active DOWNLOADER_BACKEND (ADR-039)."
    )
    parser.add_argument("--magnet", required=True, help="Magnet URI to add")
    parser.add_argument("--category", required=True, help="Torrent category / label")
    parser.add_argument(
        "--name", default=None, help="Optional torrent rename (qb backend only)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = dispatch.add(args.magnet, args.category, name=args.name)
    if result.ok:
        print(f"[ok] {result.plugin}: torrent added (category={args.category})")
        return 0
    print(
        f"[fail] {result.plugin}: {result.detail or 'unknown error'}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
