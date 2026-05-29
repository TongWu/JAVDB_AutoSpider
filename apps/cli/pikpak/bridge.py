from __future__ import annotations

import argparse
from pathlib import Path

from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.service import run_bridge
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override

REPO_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PikPak Bridge - Transfer torrents from qBittorrent to PikPak")
    parser.add_argument("--days", type=int, default=3, help="Filter torrents older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Test mode: no delete or PikPak add")
    parser.add_argument("--individual", action="store_true", help="Process torrents individually instead of batch mode (default: batch mode)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for PikPak and qBittorrent requests in this command",
        no_help="Force-disable proxy for PikPak and qBittorrent requests in this command",
    )
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for saving pikpak stats to SQLite")
    parser.add_argument(
        "--root-folder",
        default=None,
        help="PikPak root folder for uploads. Each torrent is placed under {root}/{qB category}. Defaults to PIKPAK_ROOT_FOLDER from config (/Javdb_AutoSpider).",
    )
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> PikPakBridgeOptions:
    return PikPakBridgeOptions(
        days=args.days,
        dry_run=args.dry_run,
        batch_mode=not args.individual,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        from_pipeline=args.from_pipeline,
        session_id=args.session_id,
        root_folder=args.root_folder,
    )


def main(argv: list[str] | None = None) -> int:
    import os

    os.chdir(REPO_ROOT)
    # Preserve the legacy CLI lifecycle: ensure the DB connection is closed at
    # process exit (the former bridge.main() registered this).
    import atexit

    from javdb.storage.db import close_db

    atexit.register(close_db)
    return run_bridge(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
