"""Inspect and compact the inert ADR-010 D1 recovery outbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from javdb.storage.d1_recovery import RecoveryEvent, compact_replayed, outbox_status


def _default_outbox_path() -> str:
    return os.path.join(
        os.environ.get("REPORTS_DIR", "reports"),
        "D1",
        "d1_recovery_outbox.jsonl",
    )


def _default_processed_path(outbox: str) -> str:
    outbox_path = Path(outbox)
    return str(outbox_path.with_name("d1_recovery_outbox.processed.jsonl"))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.d1_recovery",
        description=(
            "Inspect or compact the ADR-010 D1 recovery outbox. "
            "Replay is intentionally not implemented in Phase 1."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser(
        "inspect",
        help="Summarise pending D1 recovery work without mutating the outbox.",
    )
    inspect.add_argument(
        "--outbox",
        default=None,
        help=(
            "Path to d1_recovery_outbox.jsonl. Defaults to "
            "$REPORTS_DIR/D1/d1_recovery_outbox.jsonl."
        ),
    )
    inspect.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output summary as JSON.",
    )

    compact = subparsers.add_parser(
        "compact",
        help="Move replayed/abandoned event histories to the processed JSONL.",
    )
    compact.add_argument(
        "--outbox",
        default=None,
        help=(
            "Path to d1_recovery_outbox.jsonl. Defaults to "
            "$REPORTS_DIR/D1/d1_recovery_outbox.jsonl."
        ),
    )
    compact.add_argument(
        "--processed",
        default=None,
        help=(
            "Path to processed JSONL. Defaults to sibling "
            "d1_recovery_outbox.processed.jsonl."
        ),
    )
    compact.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output compaction result as JSON.",
    )

    return parser


def _event_summary(event: RecoveryEvent) -> Dict[str, object]:
    return {
        "idempotency_key": event.idempotency_key,
        "logical_db": event.logical_db,
        "operation_type": event.operation_type,
        "state": event.state,
        "attempt": event.attempt,
        "recovery_allowed": event.recovery_allowed,
        "max_attempts": event.max_attempts,
    }


def _group_summary(
    grouped: Dict[str, List[RecoveryEvent]],
) -> Dict[str, List[Dict[str, object]]]:
    return {
        ordering_key: [
            _event_summary(event)
            for event in events
        ]
        for ordering_key, events in grouped.items()
    }


def _inspect_summary(outbox: str) -> Dict[str, object]:
    status = outbox_status(outbox)
    pending_groups = _group_summary(status["pending_groups"])
    dead_lettered_groups = _group_summary(status["dead_lettered_groups"])
    return {
        "outbox": outbox,
        "pending_count": status["pending_count"],
        "dead_lettered_count": status["dead_lettered_count"],
        "malformed_count": status["malformed_count"],
        "ordering_key_count": status["ordering_key_count"],
        "pending_groups": pending_groups,
        "dead_lettered_groups": dead_lettered_groups,
        "latest_state_counts": status["latest_state_counts"],
    }


def _format_inspect(summary: Dict[str, object]) -> str:
    pending_count = int(summary["pending_count"])
    lines = [
        f"Outbox: {summary['outbox']}",
        f"Pending events: {pending_count}",
        f"Dead-lettered events: {summary['dead_lettered_count']}",
        f"Malformed lines: {summary['malformed_count']}",
        f"Ordering keys: {summary['ordering_key_count']}",
    ]
    pending_groups = summary["pending_groups"]
    dead_lettered_groups = summary["dead_lettered_groups"]
    if (
        not isinstance(pending_groups, dict)
        or not isinstance(dead_lettered_groups, dict)
        or (not pending_groups and not dead_lettered_groups)
    ):
        lines.append("No pending recovery work.")
        return "\n".join(lines)

    if pending_groups:
        lines.append("")
        lines.append("Pending:")
    for ordering_key, events in pending_groups.items():
        lines.append(f"  {ordering_key}: {len(events)} pending")
        for event in events:
            lines.append(
                "    - "
                f"{event['idempotency_key']} "
                f"state={event['state']} "
                f"attempt={event['attempt']}/{event['max_attempts']} "
                f"operation={event['operation_type']}"
            )
    if dead_lettered_groups:
        lines.append("")
        lines.append("Dead-lettered:")
    for ordering_key, events in dead_lettered_groups.items():
        lines.append(f"  {ordering_key}: {len(events)} dead_lettered")
        for event in events:
            lines.append(
                "    - "
                f"{event['idempotency_key']} "
                f"state={event['state']} "
                f"attempt={event['attempt']}/{event['max_attempts']} "
                f"operation={event['operation_type']}"
            )
    return "\n".join(lines)


def _format_compact(result: Dict[str, int], *, outbox: str, processed: str) -> str:
    lines = [
        f"Outbox: {outbox}",
        f"Processed: {processed}",
        f"Active lines left: {result['active']}",
        f"Processed lines moved: {result['processed']}",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    outbox = args.outbox or _default_outbox_path()

    if args.command == "inspect":
        summary = _inspect_summary(outbox)
        if args.json_output:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(_format_inspect(summary))
        has_blocking_state = (
            int(summary["pending_count"]) > 0
            or int(summary["dead_lettered_count"]) > 0
            or int(summary["malformed_count"]) > 0
        )
        return 1 if has_blocking_state else 0

    if args.command == "compact":
        processed = args.processed or _default_processed_path(outbox)
        result = compact_replayed(outbox, processed)
        if args.json_output:
            print(
                json.dumps(
                    {"outbox": outbox, "processed": processed, **result},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(_format_compact(result, outbox=outbox, processed=processed))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
