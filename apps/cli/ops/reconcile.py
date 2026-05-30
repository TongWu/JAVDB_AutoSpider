"""Reconcile acquisition outcomes against live sources (ADR-033 Phase 1).

CLI adapter only: parses args, owns exit codes. All domain logic lives in
javdb.ops.reconcile.service (Options -> Result).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.config import cfg
from javdb.infra.logging import setup_logging
from javdb.ops.reconcile.models import ReconcileOptions
from javdb.ops.reconcile.service import run

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.reconcile",
        description="Reconcile acquisition outcomes (ADR-033 media closed-loop, Phase 1).",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        default=None,
        help="Source to reconcile (repeatable). Default: qb",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=None,
        help="qB category to scan (repeatable). Default: JavDB, Ad Hoc",
    )
    parser.add_argument(
        "--stalled-after-days",
        type=int,
        default=int(cfg("RECONCILE_STALLED_DAYS", 7)),
        help="Active outcomes unseen for this many days become stalled.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute transitions but write nothing.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    options = ReconcileOptions(
        sources=tuple(args.sources or ("qb",)),
        categories=tuple(args.categories or ("JavDB", "Ad Hoc")),
        stalled_after_days=args.stalled_after_days,
        dry_run=args.dry_run,
    )
    result = run(options)

    if args.json_output:
        print(json.dumps(result.__dict__, ensure_ascii=False))
    else:
        logger.info(
            "Reconcile done: observed=%d updated=%d downloading=%d completed=%d "
            "stalled=%d failed=%d errors=%d",
            result.observed,
            result.outcomes_updated,
            result.marked_downloading,
            result.marked_completed,
            result.marked_stalled,
            result.marked_failed,
            len(result.errors),
        )

    return 2 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
