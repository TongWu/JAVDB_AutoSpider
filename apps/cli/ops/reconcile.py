"""Reconcile acquisition outcomes against live sources (ADR-033 Phase 1).

CLI adapter only: parses args, owns exit codes. All domain logic lives in
javdb.ops.reconcile.service (Options -> Result).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import sys

from javdb.infra.config import cfg
from javdb.infra.logging import log_section, log_summary_block, setup_logging
from javdb.ops.reconcile.models import ReconcileOptions
from javdb.ops.reconcile.service import run

logger = logging.getLogger(__name__)


def _default_stalled_after_days() -> int:
    try:
        value = int(cfg("RECONCILE_STALLED_DAYS", 7))
        if value < 1:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning("Invalid RECONCILE_STALLED_DAYS; falling back to 7")
        return 7
    return value


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer >= 1") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer >= 1")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.reconcile",
        description="Reconcile acquisition outcomes (ADR-033 media closed-loop, Phase 1).",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=("qb",),
        default=None,
        help="Source to reconcile (repeatable). Phase 1 supports qb. Default: qb",
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
        type=_positive_int,
        default=None,
        help="Active outcomes unseen for this many days become stalled; must be >= 1.",
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
    try:
        args = _build_parser().parse_args(argv)
        setup_logging(log_level=args.log_level)
        stalled_after_days = (
            args.stalled_after_days
            if args.stalled_after_days is not None
            else _default_stalled_after_days()
        )

        options = ReconcileOptions(
            sources=tuple(args.sources or ("qb",)),
            categories=tuple(args.categories or ("JavDB", "Ad Hoc")),
            stalled_after_days=stalled_after_days,
            dry_run=args.dry_run,
        )
        result = run(options)

        if args.json_output:
            print(json.dumps(asdict(result), ensure_ascii=False))
        else:
            log_section(logger, "Acquisition Outcome Reconcile")
            log_summary_block(logger, "Reconcile Summary", {
                "Observed": result.observed,
                "Outcomes updated": result.outcomes_updated,
                "Marked downloading": result.marked_downloading,
                "Marked completed": result.marked_completed,
                "Marked stalled": result.marked_stalled,
                "Marked failed": result.marked_failed,
                "Errors": len(result.errors),
            })

        return 2 if result.errors else 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
