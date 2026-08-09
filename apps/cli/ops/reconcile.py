"""Reconcile acquisition outcomes against live sources (ADR-033 Phase 1+2+3).

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
from javdb.ops.reconcile.media_config import parse_media_servers
from javdb.ops.reconcile.models import ConsumptionOptions, OwnershipOptions, ReconcileOptions
from javdb.ops.reconcile.service import run, run_consumption, run_ownership

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


def _default_categories() -> tuple[str, ...]:
    categories = []
    for value in (
        cfg("TORRENT_CATEGORY", "JavDB"),
        cfg("TORRENT_CATEGORY_ADHOC", "Ad Hoc"),
    ):
        category = str(value or "").strip()
        if category and category not in categories:
            categories.append(category)
    return tuple(categories or ("JavDB", "Ad Hoc"))


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
        description="Reconcile acquisition outcomes, ownership ledger, and consumption signal (ADR-033 media closed-loop, Phase 1+2+3).",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=("qb",),
        default=None,
        help="Source to reconcile (repeatable). Currently supports: qb. Default: qb",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=None,
        help=(
            "qB category to scan (repeatable). Default: TORRENT_CATEGORY and "
            "TORRENT_CATEGORY_ADHOC from config.py"
        ),
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
    parser.add_argument(
        "--pass", dest="pass_name", default="all",
        choices=("acquisition", "ownership", "consumption", "all"),
        help="Which reconcile pass to run. Default: all (acquisition then ownership then consumption).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        setup_logging(log_level=args.log_level)

        acquisition_result = None
        ownership_result = None
        consumption_result = None

        if args.pass_name in ("acquisition", "all"):
            stalled_after_days = (
                args.stalled_after_days
                if args.stalled_after_days is not None
                else _default_stalled_after_days()
            )
            categories = (
                tuple(args.categories)
                if args.categories is not None
                else _default_categories()
            )
            options = ReconcileOptions(
                sources=tuple(args.sources or ("qb",)),
                categories=categories,
                stalled_after_days=stalled_after_days,
                dry_run=args.dry_run,
                infer_absent=args.categories is None,
            )
            acquisition_result = run(options)

        if args.pass_name in ("ownership", "all"):
            ownership_options = OwnershipOptions(dry_run=args.dry_run)
            ownership_result = run_ownership(ownership_options)

        consumption_config_error = False
        if args.pass_name in ("consumption", "all"):
            try:
                servers = parse_media_servers(cfg("MEDIA_SERVERS", []))
                consumption_options = ConsumptionOptions(servers=servers, dry_run=args.dry_run)
                consumption_result = run_consumption(consumption_options)
            except ValueError as exc:
                print(f"Error: invalid MEDIA_SERVERS config: {exc}", file=sys.stderr)
                consumption_config_error = True

        if args.json_output:
            output: dict = {}
            if acquisition_result is not None:
                output["acquisition"] = asdict(acquisition_result)
            if ownership_result is not None:
                output["ownership"] = asdict(ownership_result)
            if consumption_result is not None:
                output["consumption"] = asdict(consumption_result)
            print(json.dumps(output, ensure_ascii=False))
        else:
            if acquisition_result is not None:
                log_section(logger, "Acquisition Outcome Reconcile")
                log_summary_block(logger, "Reconcile Summary", {
                    "Observed": acquisition_result.observed,
                    "Outcomes updated": acquisition_result.outcomes_updated,
                    "Marked downloading": acquisition_result.marked_downloading,
                    "Marked completed": acquisition_result.marked_completed,
                    "Marked stalled": acquisition_result.marked_stalled,
                    "Marked failed": acquisition_result.marked_failed,
                    "Errors": len(acquisition_result.errors),
                })
            if ownership_result is not None:
                log_section(logger, "Ownership Ledger Reconcile")
                log_summary_block(logger, "Ownership Summary", {
                    "Observed": ownership_result.observed,
                    "Upserted": ownership_result.upserted,
                    "Swept absent": ownership_result.swept_absent,
                    "Marked in-library": ownership_result.marked_in_library,
                    "Errors": len(ownership_result.errors),
                })
            if consumption_result is not None:
                log_section(logger, "Consumption Signal Reconcile")
                log_summary_block(logger, "Consumption Summary", {
                    "Instances observed": consumption_result.instances_observed,
                    "Items observed": consumption_result.items_observed,
                    "Signals updated": consumption_result.signals_updated,
                    "Resolved high/medium/low": (
                        f"{consumption_result.resolved_high}/"
                        f"{consumption_result.resolved_medium}/{consumption_result.resolved_low}"
                    ),
                    "Marked unresolved": consumption_result.marked_unresolved,
                    "Errors": len(consumption_result.errors),
                })

        has_errors = (
            (acquisition_result is not None and acquisition_result.errors)
            or (ownership_result is not None and ownership_result.errors)
            or (consumption_result is not None and consumption_result.errors)
        )
        if consumption_config_error:
            return 1
        return 2 if has_errors else 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
