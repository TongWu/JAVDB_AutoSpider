"""Run the event-spine demonstrator consumer (ADR-036 Phase 1).

Reads new PipelineEvent rows by cursor and projects per-session counts into
RunEventSummary. --replay resets the cursor + projection and rebuilds from seq 0."""

from __future__ import annotations

import argparse
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.pipeline.events.consumer import RunEventSummaryConsumer
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo, RunEventSummaryRepo

logger = logging.getLogger(__name__)


def _positive_int(value: str) -> int:
    """argparse type: reject non-positive --batch (a 0/negative page size would
    make run_once read zero rows forever and never satisfy n < batch)."""
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.events",
        description="Project pipeline events into RunEventSummary (ADR-036).",
    )
    p.add_argument("--replay", action="store_true",
                   help="Reset the consumer cursor + projection, then rebuild from seq 0.")
    p.add_argument("--batch", type=_positive_int, default=500,
                   help="Events to read per page (must be >= 1).")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)
    with get_db(_db.REPORTS_DB_PATH) as conn:
        event_repo = PipelineEventRepo(conn)
        consumer = RunEventSummaryConsumer(RunEventSummaryRepo(conn))
        if args.replay:
            event_repo.advance_cursor(consumer.name, 0)
            RunEventSummaryRepo(conn).reset()
            logger.info("Replay: cursor + projection reset")
        total = 0
        while True:
            n = consumer.run_once(event_repo=event_repo, batch=args.batch)
            total += n
            if n < args.batch:
                break
    logger.info("Projected %d event(s)", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
