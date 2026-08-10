#!/usr/bin/env python3
"""Ops CLI: clear cross-runner proxy bans on the shared proxy coordinator.

Bans propagate cross-runner via the Worker (P1-A in
``javdb/proxy/ban_manager.py``) with TTLs up to 8 days for a JavDB hard ban
(ADR-043 D9). A burst of transient failures unrelated to actual proxy health
-- e.g. the CF_BYPASS_VIA_PROXY connectivity gap fixed in BFR-024, or a
period of manual load-testing against the proxy hosts directly -- can poison
that shared state for proxies that are demonstrably fine again. Waiting out
the TTL is the safe default; this script is for when that's been verified
independently (e.g. a successful manual fetch through the proxy) and the
wait isn't worth it.

Usage:
    python -m apps.cli.ops.proxy_unban --all
    python -m apps.cli.ops.proxy_unban --proxy Jeddah-ARM1 --proxy Jeddah-ARM2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.logging import setup_logging, get_logger

setup_logging()
logger = get_logger("proxy_unban")

from javdb.infra.config import cfg
from javdb.proxy.coordinator.proxy_coordinator_client import (
    CoordinatorUnavailable,
    ProxyCoordinatorClient,
)

PROXY_COORDINATOR_URL = cfg("PROXY_COORDINATOR_URL", "")
PROXY_COORDINATOR_TOKEN = cfg("PROXY_COORDINATOR_TOKEN", "")
PROXY_POOL = cfg("PROXY_POOL", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="Unban every proxy in PROXY_POOL"
    )
    parser.add_argument(
        "--proxy",
        action="append",
        default=[],
        help="Unban a specific proxy name (repeatable)",
    )
    parser.add_argument(
        "--reason",
        default="ops manual clear",
        help="Free-form annotation stored with the unban event",
    )
    args = parser.parse_args()

    if not PROXY_COORDINATOR_URL or not PROXY_COORDINATOR_TOKEN:
        logger.error(
            "PROXY_COORDINATOR_URL/PROXY_COORDINATOR_TOKEN not configured "
            "-- nothing to do"
        )
        return 1

    if args.all:
        names = [cfg.get("name", "") for cfg in PROXY_POOL if cfg.get("name")]
    else:
        names = args.proxy

    if not names:
        parser.error("pass --all or at least one --proxy NAME")

    client = ProxyCoordinatorClient(
        base_url=PROXY_COORDINATOR_URL, token=PROXY_COORDINATOR_TOKEN
    )
    failures = 0
    try:
        for name in names:
            try:
                client.report(name, kind="unban", reason=args.reason)
                logger.info("unbanned: %s", name)
            except CoordinatorUnavailable as e:
                failures += 1
                logger.error("FAILED to unban %s: %s", name, e)
    finally:
        client.close()

    logger.info("done: %d/%d succeeded", len(names) - failures, len(names))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
