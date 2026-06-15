#!/usr/bin/env python3
"""CLI entry: scrape followed actors and write the new-works feed (ADR-054 WS2)."""

from __future__ import annotations

import argparse
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.pipeline.subscription_monitor import run_subscription_monitor
from javdb.storage.db import init_db
from javdb.storage.repos.subscription_repo import ActorSubscriptionRepo

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.subscription_monitor",
        description="Scrape followed actors and write the new-works feed.",
    )
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="Route scrapes through the proxy pool.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List active subscriptions without scraping.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)
    init_db()

    if args.dry_run:
        active = ActorSubscriptionRepo().list_active_hrefs()
        logger.info(
            "Active subscriptions (%d): %s",
            len(active),
            ", ".join(active) or "(none)",
        )
        return 0

    added = run_subscription_monitor(use_proxy=args.use_proxy)
    logger.info("Subscription monitor added %d new feed row(s).", added)
    return 0


if __name__ == "__main__":
    sys.exit(main())
