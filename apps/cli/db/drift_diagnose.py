"""CLI for diagnosing and fixing pending-write drift.

The D1/SQLite discovery, classification, deletion, and audit logic lives in
``javdb.storage.drift_diagnose``.  This module is the executable boundary: it
parses arguments, formats diagnose output, and maps service results to process
exit codes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from javdb.infra.logging import setup_logging
from javdb.storage import drift_diagnose as drift_service


def format_output(results: List[dict], *, as_json: bool) -> str:
    """Format diagnosis results for CLI output."""
    verdicts = [r.get("verdict", drift_service.VERDICT_CLEAN) for r in results]
    max_verdict = drift_service.VERDICT_CLEAN
    max_code = 0
    for verdict in verdicts:
        code = drift_service.VERDICT_EXIT_CODE.get(verdict, 0)
        if code > max_code:
            max_code = code
            max_verdict = verdict

    if as_json:
        output = {
            "suspects": results,
            "max_verdict": max_verdict,
        }
        return json.dumps(output, indent=2, ensure_ascii=False)

    lines: List[str] = []
    if not results:
        lines.append("No suspect sessions found.")
    else:
        lines.append(f"Found {len(results)} suspect session(s):")
        lines.append("")
        for result in results:
            lines.append(f"  Session: {result['session_id']}")
            lines.append(f"    Provenance:          {result['provenance']}")
            lines.append(f"    Verdict:             {result['verdict']}")
            lines.append(
                f"    D1 orphan movies:    "
                f"{result.get('d1_orphan_movie_count', 0)}"
            )
            lines.append(
                f"    D1 orphan torrents:  "
                f"{result.get('d1_orphan_torrent_count', 0)}"
            )
            if "suggested_command" in result:
                lines.append(f"    Suggested fix:       {result['suggested_command']}")
            if "note" in result:
                lines.append(f"    Note:                {result['note']}")
            lines.append("")
        lines.append(f"Max verdict: {max_verdict}")

    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.drift_diagnose",
        description=(
            "Diagnose pending-write drift between D1 and local SQLite. "
            "Read-only by default; use --apply --session-id to fix orphans."
        ),
    )

    diag = parser.add_argument_group("diagnose options")
    diag.add_argument(
        "--since",
        type=float,
        default=24.0,
        metavar="HOURS",
        help="Look-back window in hours (default: 24).",
    )
    diag.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON for programmatic consumption.",
    )
    diag.add_argument(
        "--drift-log",
        default=None,
        help=(
            "Path to d1_drift.jsonl. Defaults to "
            "$REPORTS_DIR/D1/d1_drift.jsonl."
        ),
    )
    diag.add_argument(
        "--history-db",
        default=None,
        help=(
            "Path to local history.db for live-table comparison. "
            "Defaults to $REPORTS_DIR/history.db. "
            "Omit or set empty to skip SQLite comparison."
        ),
    )

    apply_group = parser.add_argument_group("apply options")
    apply_group.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Delete orphan Pending*HistoryWrites rows for a committed "
            "session whose verdict is SAFE_TO_APPLY. Requires --session-id."
        ),
    )
    apply_group.add_argument(
        "--session-id",
        default=None,
        metavar="SESSION_ID",
        help="Target session for --apply.",
    )
    apply_group.add_argument(
        "--max-deletes",
        type=int,
        default=100,
        metavar="N",
        help=(
            "Maximum total orphan rows allowed for --apply (default: 100). "
            "Protects against accidental bulk DELETEs."
        ),
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO).",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the drift diagnosis CLI."""
    args = _build_arg_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    history_db_path = args.history_db
    if history_db_path is None:
        history_db_path = os.path.join(reports_dir, "history.db")

    if args.apply:
        if not args.session_id:
            print(
                "ERROR: --apply requires --session-id. "
                "Specify the session to fix.",
                file=sys.stderr,
            )
            return 2

        return drift_service.apply_fix(
            session_id=args.session_id,
            sqlite_history_path=history_db_path,
            max_deletes=args.max_deletes,
        )

    drift_log_path = args.drift_log or os.path.join(
        reports_dir, "D1", "d1_drift.jsonl",
    )
    results, exit_code = drift_service.diagnose(
        drift_log_path=drift_log_path,
        since_hours=args.since,
        sqlite_history_path=history_db_path,
    )

    print(format_output(results, as_json=args.json_output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
