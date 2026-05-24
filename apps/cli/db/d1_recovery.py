"""Inspect and compact the inert ADR-010 D1 recovery outbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from javdb.storage.d1_recovery import compact_replayed, pending_by_ordering_key


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


def _inspect_summary(outbox: str) -> Dict[str, object]:
    grouped = pending_by_ordering_key(outbox)
    groups = {
        ordering_key: [
            {
                "idempotency_key": event.idempotency_key,
                "logical_db": event.logical_db,
                "operation_type": event.operation_type,
                "state": event.state,
                "attempt": event.attempt,
                "recovery_allowed": event.recovery_allowed,
                "max_attempts": event.max_attempts,
            }
            for event in events
        ]
        for ordering_key, events in grouped.items()
    }
    return {
        "outbox": outbox,
        "pending_count": sum(len(events) for events in grouped.values()),
        "ordering_key_count": len(grouped),
        "groups": groups,
    }


def _format_inspect(summary: Dict[str, object]) -> str:
    pending_count = int(summary["pending_count"])
    lines = [
        f"Outbox: {summary['outbox']}",
        f"Pending events: {pending_count}",
        f"Ordering keys: {summary['ordering_key_count']}",
    ]
    groups = summary["groups"]
    if not isinstance(groups, dict) or not groups:
        lines.append("No pending recovery work.")
        return "\n".join(lines)

    lines.append("")
    for ordering_key, events in groups.items():
        lines.append(f"{ordering_key}: {len(events)} pending")
        for event in events:
            lines.append(
                "  - "
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
        return 1 if int(summary["pending_count"]) > 0 else 0

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
