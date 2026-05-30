# apps/cli/ops/sentinel.py
"""Evaluate a session's persisted field-health for site-contract drift (ADR-035).

Read-only by default; exit code 4 signals critical drift so a workflow can act."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from javdb.infra.logging import setup_logging
from javdb.ops.sentinel.service import evaluate_session

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.sentinel",
        description="Evaluate a run's parse field-health for site-contract drift.",
    )
    p.add_argument("--session-id", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--attempt", type=int, default=None, dest="run_attempt")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)
    try:
        verdict = evaluate_session(
            args.session_id, run_id=args.run_id, run_attempt=args.run_attempt)
    except Exception:
        # Internal error (e.g. DB unreachable) — exit 1 so callers can tell it
        # apart from a clean run (0) or a confirmed critical drift (4).
        logger.exception("Sentinel evaluation failed for session %s", args.session_id)
        return 1
    if args.json_output:
        print(json.dumps({
            "critical": verdict.critical,
            "evaluated": verdict.evaluated,
            "findings": [asdict(f) for f in verdict.findings],
        }, ensure_ascii=False))
    else:
        logger.info("Sentinel: critical=%s evaluated=%d findings=%d",
                    verdict.critical, verdict.evaluated, len(verdict.findings))
    return 4 if verdict.critical else 0


if __name__ == "__main__":
    sys.exit(main())
