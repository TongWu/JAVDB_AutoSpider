from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)

from javdb.infra.config import cfg
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override
from javdb.quality.collector import run_collection


def _evidence_enabled() -> bool:
    return bool(cfg("TORRENT_QUALITY_EVIDENCE_ENABLED", False))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect shadow torrent-quality evidence (ADR-024, read-only)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Days to look back for production torrents",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default=None,
        help='JSON array of qB categories (e.g. ["Daily Ingestion"])',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when TORRENT_QUALITY_EVIDENCE_ENABLED is False",
    )
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    return parser.parse_args(argv)


def _parse_categories(raw_categories: str | None) -> list[str] | None:
    if raw_categories is None or not raw_categories.strip():
        return None
    categories = json.loads(raw_categories)
    if not isinstance(categories, list):
        raise argparse.ArgumentTypeError("--categories must be a JSON array")
    return [
        category
        for category in (str(category).strip() for category in categories)
        if category
    ]


def _resolve_categories(cli_categories: str | None) -> list[str] | None:
    if cli_categories is not None:
        return _parse_categories(cli_categories)
    configured = (cfg("TORRENT_QUALITY_CATEGORIES", "") or "").strip()
    if not configured:
        return None
    return _parse_categories(configured)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        categories = _resolve_categories(args.categories)
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(str(exc)) from exc

    if not args.force and not _evidence_enabled():
        print(
            "Torrent quality evidence disabled "
            "(TORRENT_QUALITY_EVIDENCE_ENABLED=False); skipping."
        )
        return 0

    summary = run_collection(
        days=args.days,
        categories=categories,
        use_proxy=resolve_proxy_override(args.use_proxy, args.no_proxy),
    )
    print(
        "Quality evidence summary: "
        f"scanned={summary.get('scanned', 0)}, "
        f"skipped={summary.get('skipped', 0)}, "
        f"evidence_written={summary.get('evidence_written', 0)}, "
        f"evaluations_written={summary.get('evaluations_written', 0)}, "
        f"probe_unavailable={summary.get('probe_unavailable', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
