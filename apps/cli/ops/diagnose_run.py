"""Read-only operations diagnosis CLI for ADR-026."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.ops.diagnosis.collectors import collect_incident_bundle
from javdb.ops.diagnosis.service import diagnose_incident


logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.diagnose_run",
        description="Collect read-only incident evidence and persist an ADR-026 diagnosis.",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--attempt", type=int, dest="run_attempt")
    parser.add_argument("--session-id")
    parser.add_argument("--workflow-name", default=None)
    parser.add_argument(
        "--workflow-result",
        default=None,
        choices=("success", "failure", "cancelled", "skipped"),
    )
    parser.add_argument("--trigger-source", default="manual_cli")
    parser.add_argument("--session-status", default=None)
    parser.add_argument("--drift-verdict", default=None)
    parser.add_argument("--log", action="append", default=[], dest="log_paths")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def _safe_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _record_to_payload(record) -> dict:
    return {
        "incident_id": record.incident_id,
        "incident_type": record.incident_type,
        "confidence": record.confidence,
        "persistence_status": record.persistence_status,
        "confirmed_findings": _safe_json_list(record.confirmed_findings_json),
        "likely_causes": _safe_json_list(record.likely_causes_json),
        "unknowns": _safe_json_list(record.unknowns_json),
        "recommended_next_actions": _safe_json_list(record.recommended_next_actions_json),
        "unsafe_actions": _safe_json_list(record.unsafe_actions_json),
        "evidence_refs": _safe_json_list(record.evidence_refs_json),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)

    if not args.run_id and not args.session_id:
        print("ERROR: provide --run-id or --session-id for diagnosis.", file=sys.stderr)
        return 2

    try:
        bundle = collect_incident_bundle(
            trigger_source=args.trigger_source,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            session_id=args.session_id,
            workflow_name=args.workflow_name,
            workflow_result=args.workflow_result,
            session_status=args.session_status,
            drift_verdict=args.drift_verdict,
            log_paths=args.log_paths,
        )
        record = diagnose_incident(bundle)
        payload = _record_to_payload(record)
    except Exception:
        logger.exception("Operations diagnosis failed unexpectedly.")
        print("ERROR: operations diagnosis failed unexpectedly.", file=sys.stderr)
        return 3

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Incident: {payload['incident_id']}")
        print(f"Type: {payload['incident_type']}")
        print(f"Confidence: {payload['confidence']}")
        print(f"Persistence: {payload['persistence_status']}")
        for finding in payload["confirmed_findings"]:
            print(f"- {finding}")

    return 1 if payload["incident_type"] != "unknown" else 0


if __name__ == "__main__":
    raise SystemExit(main())
