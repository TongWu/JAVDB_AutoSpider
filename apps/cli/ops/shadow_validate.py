"""CLI: cross-validate AcquisitionOutcomeShadow vs AcquisitionOutcome (ADR-036 P2).

Prints a JSON report comparing the event-driven shadow projection against
the authoritative acquisition table.

Exit codes: 0 = clean; 1 = discrepancies found; 2 = error during validation."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.ops.reconcile.shadow_validate import compare_shadow_to_authoritative


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.shadow_validate",
        description=(
            "Cross-validate AcquisitionOutcomeShadow (event-driven) "
            "vs AcquisitionOutcome (authoritative). ADR-036 Phase 2."
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)
    logger = logging.getLogger(__name__)

    result = compare_shadow_to_authoritative()
    report = {
        "shadow_total": result.shadow_total,
        "auth_total": result.auth_total,
        "missing_from_shadow": result.missing_from_shadow,
        "missing_from_auth": result.missing_from_auth,
        "state_mismatches": result.state_mismatches,
        "is_clean": result.is_clean,
        "errors": result.errors,
    }
    print(json.dumps(report, indent=2))

    if result.errors:
        logger.error("Validation encountered errors")
        return 2
    if not result.is_clean:
        logger.warning(
            "Discrepancies found: missing_from_shadow=%d missing_from_auth=%d "
            "state_mismatches=%d",
            len(result.missing_from_shadow),
            len(result.missing_from_auth),
            len(result.state_mismatches),
        )
        return 1
    logger.info("Shadow projection is clean (no discrepancies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
