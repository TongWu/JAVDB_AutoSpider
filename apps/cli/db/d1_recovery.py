"""Inspect, replay, and compact the ADR-010 D1 recovery outbox."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from javdb.infra.logging import log_section, log_summary_block
from javdb.storage.d1_recovery import (
    RecoveryEvent,
    compact_replayed,
    outbox_status,
    pending_by_ordering_key,
    replay_ordering_key,
    startup_drain,
)

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "value must be a positive integer"
        ) from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return value


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
            "Inspect, replay, or compact the ADR-010 D1 recovery outbox."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_outbox_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--outbox",
            default=None,
            help=(
                "Path to d1_recovery_outbox.jsonl. Defaults to "
                "$REPORTS_DIR/D1/d1_recovery_outbox.jsonl."
            ),
        )

    inspect = subparsers.add_parser(
        "inspect",
        help="Summarise pending D1 recovery work without mutating the outbox.",
    )
    add_outbox_args(inspect)
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
    add_outbox_args(compact)
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

    replay = subparsers.add_parser(
        "replay",
        help="Replay pending recovery work for one ordering key or all keys.",
    )
    add_outbox_args(replay)
    replay.add_argument(
        "--processed",
        default=None,
        help=(
            "Path to processed JSONL. Defaults to sibling "
            "d1_recovery_outbox.processed.jsonl."
        ),
    )
    target = replay.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--ordering-key",
        help="Replay one FIFO ordering key such as history:<session_id>.",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Replay all non-dead-lettered pending ordering keys.",
    )
    replay.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output replay result as JSON.",
    )

    startup = subparsers.add_parser(
        "startup-drain",
        help="Run the same bounded drain used by D1 startup replay.",
    )
    add_outbox_args(startup)
    startup.add_argument(
        "--processed",
        default=None,
        help=(
            "Path to processed JSONL. Defaults to sibling "
            "d1_recovery_outbox.processed.jsonl."
        ),
    )
    startup.add_argument(
        "--max-ordering-keys",
        type=_positive_int,
        default=None,
        help=(
            "Optional positive integer cap on ordering keys drained in this "
            "invocation."
        ),
    )
    startup.add_argument(
        "--max-events-per-key",
        type=_positive_int,
        default=None,
        help=(
            "Optional positive integer cap on events replayed per ordering "
            "key."
        ),
    )
    startup.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output drain result as JSON.",
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


def _make_connection_for_key(outbox: str, ordering_key: str):
    events = pending_by_ordering_key(outbox).get(ordering_key, [])
    if not events:
        return _NoopConnection()
    logical_dbs = {event.logical_db for event in events}
    if len(logical_dbs) != 1:
        raise RuntimeError(
            f"ordering key {ordering_key!r} spans multiple logical DBs: "
            f"{sorted(logical_dbs)}"
        )
    from javdb.storage.d1_client import make_d1_connection

    return make_d1_connection(next(iter(logical_dbs)))


class _NoopConnection:
    def execute(self, sql, params=()):
        raise RuntimeError(f"no pending recovery event exists for SQL {sql!r}")


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    outbox = args.outbox or _default_outbox_path()

    if args.command == "inspect":
        summary = _inspect_summary(outbox)
        if args.json_output:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            log_section(logger, "Recovery Outbox Inspect")
            log_summary_block(logger, "Outbox Status", {
                "Outbox": summary["outbox"],
                "Pending events": summary["pending_count"],
                "Dead-lettered events": summary["dead_lettered_count"],
                "Malformed lines": summary["malformed_count"],
                "Ordering keys": summary["ordering_key_count"],
            })
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
            log_section(logger, "Recovery Outbox Compact")
            log_summary_block(logger, "Compact Result", {
                "Outbox": outbox,
                "Processed": processed,
                "Active lines left": result["active"],
                "Processed lines moved": result["processed"],
            })
        return 0

    if args.command == "replay":
        processed = args.processed or _default_processed_path(outbox)
        if args.ordering_key:
            result = replay_ordering_key(
                outbox,
                processed,
                args.ordering_key,
                _make_connection_for_key(outbox, args.ordering_key),
            )
            result = {"ordering_keys": 1 if sum(result.values()) else 0, **result}
        else:
            from javdb.storage.d1_client import make_d1_connection

            result = startup_drain(
                outbox,
                processed,
                connection_factory=make_d1_connection,
            )
        if args.json_output:
            print(
                json.dumps(
                    {"outbox": outbox, "processed": processed, **result},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            log_section(logger, "Recovery Outbox Replay")
            log_summary_block(logger, "Replay Result", {
                "Outbox": outbox,
                "Processed": processed,
                "Ordering keys": result.get("ordering_keys", 0),
                "Replayed events": result.get("replayed", 0),
                "Dead-lettered events": result.get("dead_lettered", 0),
            })
        return 1 if int(result.get("dead_lettered", 0)) > 0 else 0

    if args.command == "startup-drain":
        processed = args.processed or _default_processed_path(outbox)
        from javdb.storage.d1_client import make_d1_connection

        result = startup_drain(
            outbox,
            processed,
            connection_factory=make_d1_connection,
            max_ordering_keys=args.max_ordering_keys,
            max_events_per_key=args.max_events_per_key,
        )
        if args.json_output:
            print(
                json.dumps(
                    {"outbox": outbox, "processed": processed, **result},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            log_section(logger, "Recovery Outbox Startup Drain")
            log_summary_block(logger, "Startup Drain Result", {
                "Outbox": outbox,
                "Processed": processed,
                "Ordering keys": result.get("ordering_keys", 0),
                "Replayed events": result.get("replayed", 0),
                "Dead-lettered events": result.get("dead_lettered", 0),
            })
        return 1 if int(result.get("dead_lettered", 0)) > 0 else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
