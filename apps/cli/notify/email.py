"""Canonical email notification CLI entrypoint.

Owns argparse parsing and exit-code mapping for the post-pipeline email
sender. The orchestration lives in
:mod:`javdb.integrations.notify.email.service`.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import logging
import os
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# Establish repo-root cwd BEFORE importing the integration package: its __init__
# imports the service whose module-level setup_logging()/cfg() must run at repo root.
os.chdir(REPO_ROOT)

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.service import run_email_notification
from javdb.integrations.notify import dispatch
from javdb.integrations.notify.plugin import NotifyMessage

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Email Notification for JavDB Pipeline")
    parser.add_argument("--csv-path", type=str, help="Path to the CSV file to attach")
    parser.add_argument("--mode", type=str, choices=["daily", "adhoc"], default="daily", help="Pipeline mode: daily or adhoc (default: daily)")
    parser.add_argument("--dry-run", action="store_true", help="Print email content without sending")
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for fetching stats from SQLite")
    parser.add_argument("--verify-jsonl", type=str, default=None, help="Path to reports/D1/d1_drift.jsonl. When provided, the email renders a 'Pending Mode Verification' section using the pending_session_verify records and may prefix the subject with [PENDING-ALERT] / [PENDING-PAUSE]. Defaults to $REPORTS_DIR/D1/d1_drift.jsonl when the file exists.")
    parser.add_argument("--health-snapshot", type=str, default=None, help="Path to reports/D1/pending_health_24h.json (Phase 3 Health Snapshot). When provided, an additional 24h aggregate block is rendered after Pending Mode Verification.")
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> EmailNotificationOptions:
    return EmailNotificationOptions(
        csv_path=args.csv_path,
        mode=args.mode,
        dry_run=args.dry_run,
        from_pipeline=args.from_pipeline,
        session_id=args.session_id,
        verify_jsonl=args.verify_jsonl,
        health_snapshot=args.health_snapshot,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline notification step, fanning out to all active backends.

    ADR-039 D4 wiring: ``email`` keeps its rich HTML report via
    ``run_email_notification``; every other active ``NOTIFY_BACKENDS`` entry
    (e.g. ``telegram``) receives a condensed ``NotifyMessage`` summary through
    the dispatcher, with per-backend failure isolation. When ``email`` is not
    an active backend, the rich report is computed (for the summary) but not
    delivered, and the exit code reflects the secondary fan-out rather than SMTP.
    """
    opts = options_from_args(parse_args(argv))
    active = dispatch.active_names()
    email_active = "email" in active
    secondary = [name for name in active if name != "email"]

    result = None
    if email_active or secondary:
        result = run_email_notification(opts, deliver=email_active)

    if secondary and result is not None:
        message = NotifyMessage(
            subject=result.subject,
            body=result.summary or result.subject,
            level="error" if result.has_critical_errors else "info",
        )
        for outcome in dispatch.send(message, exclude={"email"}):
            if outcome.ok:
                logger.info("Notify backend '%s' delivered run summary.", outcome.plugin)
            else:
                logger.warning(
                    "Notify backend '%s' failed: %s", outcome.plugin, outcome.detail
                )

    # Exit code is gated by the email channel's SMTP delivery. A telegram-only
    # run uses deliver=False, so result.exit_code is 0 there (a skipped send is
    # not a failure); secondary-backend failures are isolated (logged above),
    # never fatal — consistent with the dispatch fan-out contract.
    return result.exit_code if result is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
