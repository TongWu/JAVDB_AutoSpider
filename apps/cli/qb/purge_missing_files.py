"""CLI: purge ``missingFiles`` torrents from all configured qB instances.

Usage:
    python -m apps.cli.qb.purge_missing_files            # delete
    python -m apps.cli.qb.purge_missing_files --dry-run  # list only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Establish repo-root cwd before importing the integration package (its
# module-level cfg()/logging setup expects to run at the repo root).
os.chdir(REPO_ROOT)

from javdb.infra.logging import setup_logging  # noqa: E402
from javdb.integrations.qb.purge_missing_files import run_purge_missing_files  # noqa: E402


def _non_negative_hours(value: str) -> float:
    """argparse type: a finite, non-negative number of hours (the safety gate)."""
    hours = float(value)
    if not math.isfinite(hours) or hours < 0:
        raise argparse.ArgumentTypeError("--min-age-hours must be a finite number >= 0")
    return hours


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete missingFiles torrents from the primary and adhoc qBittorrent instances",
    )
    parser.add_argument("--dry-run", action="store_true", help="List missingFiles torrents without deleting")
    parser.add_argument("--min-age-hours", type=_non_negative_hours, default=22.0, help="Only consider torrents that completed at least this many hours ago (default: 22)")
    parser.add_argument("--json", action="store_true", help="Emit the per-instance summary as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    results = run_purge_missing_files(dry_run=args.dry_run, min_age_hours=args.min_age_hours)
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    # Fail the run only if the (fatal) primary instance errored.
    primary = next((r for r in results if r.get("label") == "Primary"), None)
    return 1 if primary is None or primary.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
