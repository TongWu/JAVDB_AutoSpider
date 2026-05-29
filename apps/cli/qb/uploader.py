from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Establish repo-root cwd BEFORE importing the integration package: importing any
# submodule runs the package __init__, which imports the service whose
# module-level setup_logging()/cfg() must run with cwd == repo root.
os.chdir(REPO_ROOT)

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.service import run_uploader
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="qBittorrent Uploader")
    parser.add_argument("--mode", choices=["adhoc", "daily"], default="daily", help="Upload mode: adhoc (Ad Hoc folder) or daily (Daily Report folder)")
    parser.add_argument("--input-file", type=str, help="Specify input CSV file name (overrides default date-based name)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--category", type=str, help="Override qBittorrent category (defaults to TORRENT_CATEGORY_ADHOC for adhoc mode, TORRENT_CATEGORY for daily mode)")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for saving uploader stats to SQLite")
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> QbUploaderOptions:
    return QbUploaderOptions(
        mode=args.mode,
        input_file=args.input_file,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        from_pipeline=args.from_pipeline,
        category=args.category,
        session_id=args.session_id,
    )


def main(argv: list[str] | None = None) -> int:
    return run_uploader(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
