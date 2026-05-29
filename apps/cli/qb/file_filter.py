from __future__ import annotations

import argparse
import json

from javdb.infra.config import cfg
from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.service import run_file_filter_cli
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override

# Mirror the legacy module-level default sourcing (was file_filter.py:51).
# QB_FILE_FILTER_MIN_SIZE_MB is NOT exported from javdb.integrations.qb.config;
# the original file computed it via cfg() at import time. Preserve that exactly.
QB_FILE_FILTER_MIN_SIZE_MB = cfg("QB_FILE_FILTER_MIN_SIZE_MB", 100)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter out small files from recently added torrents in qBittorrent"
    )
    parser.add_argument("--min-size", type=float, default=QB_FILE_FILTER_MIN_SIZE_MB, help=f"Minimum file size in MB (files smaller than this will be skipped). Default: {QB_FILE_FILTER_MIN_SIZE_MB}MB")
    parser.add_argument("--days", type=int, default=2, help="Number of days to look back for recently added torrents (default: 2 for today and yesterday)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be filtered without actually making changes")
    parser.add_argument("--category", type=str, default=None, help="Filter only torrents in this category (default: all categories). Deprecated: use --categories instead.")
    parser.add_argument("--categories", type=str, default=None, help="JSON array of categories to filter (e.g., '[\"Ad Hoc\", \"Daily Ingestion\"]'). If specified, overrides --category.")
    parser.add_argument("--delete-local-files", action="store_true", help="Delete local files that have already been downloaded but are below the size threshold")
    return parser.parse_args(argv)


def _parse_categories(raw_categories: str | None) -> list[str] | None:
    if not raw_categories:
        return None
    categories = json.loads(raw_categories)
    if not isinstance(categories, list):
        raise argparse.ArgumentTypeError("--categories must be a JSON array")
    return [str(category) for category in categories if category]


def options_from_args(args: argparse.Namespace) -> QbFileFilterOptions:
    return QbFileFilterOptions(
        min_size_mb=args.min_size,
        days=args.days,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        dry_run=args.dry_run,
        category=args.category,
        categories=_parse_categories(args.categories),
        delete_local_files=args.delete_local_files,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        options = options_from_args(parse_args(argv))
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(str(exc)) from exc
    return run_file_filter_cli(options).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
