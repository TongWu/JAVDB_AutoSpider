"""Canonical email notification CLI entrypoint.

Owns argparse parsing and exit-code mapping for the post-pipeline email
sender. The orchestration lives in
:mod:`javdb.integrations.notify.email.service`.
"""

from __future__ import annotations

from pathlib import Path
import argparse
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
    return run_email_notification(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
