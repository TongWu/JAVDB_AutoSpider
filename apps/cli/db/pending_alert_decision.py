"""Decide whether a run has a critical pending-mode alert."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable, Optional

from javdb.storage.sessions.pending_verify import (
    F_CLEANUP_PATH_MISMATCH_COUNT,
    F_DERIVED_RECOMPUTE_DRIFT,
    F_KIND,
    F_PENDING_RESIDUAL_COUNT,
    F_RUN_ATTEMPT,
    F_RUN_ID,
    F_SESSION_ID,
    F_STATS_READ_ERROR,
    KIND_PENDING_SESSION_VERIFY,
)

CRITICAL_PENDING_FIELDS = (
    F_PENDING_RESIDUAL_COUNT,
    F_DERIVED_RECOMPUTE_DRIFT,
    F_CLEANUP_PATH_MISMATCH_COUNT,
    F_STATS_READ_ERROR,
)


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Malformed JSONL in {path} at line {line_no}: {exc.msg}"
                ) from exc
            if isinstance(record, dict):
                yield record


def decide_pending_alert(
    jsonl_path: str | os.PathLike[str],
    run_id: str,
    run_attempt: str,
) -> str:
    """Return the workflow pause decision string, or ``""`` when clean."""
    path = Path(jsonl_path)
    for record in _iter_jsonl(path):
        if record.get(F_KIND) != KIND_PENDING_SESSION_VERIFY:
            continue
        if str(record.get(F_RUN_ID) or "") != str(run_id):
            continue
        if str(record.get(F_RUN_ATTEMPT) or "") != str(run_attempt):
            continue
        for key in CRITICAL_PENDING_FIELDS:
            try:
                if int(record.get(key) or 0) > 0:
                    return (
                        f"{key}={record.get(key)} "
                        f"session={record.get(F_SESSION_ID)}"
                    )
            except (TypeError, ValueError):
                continue
    return ""


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.pending_alert_decision",
        description="Print the ADR-006 critical pending-alert decision.",
    )
    parser.add_argument("--jsonl", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-attempt", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    jsonl_path = args.jsonl or os.path.join(
        reports_dir,
        "D1",
        "d1_drift.jsonl",
    )
    decision = decide_pending_alert(
        jsonl_path,
        args.run_id or os.environ.get("GITHUB_RUN_ID", ""),
        args.run_attempt or os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    )
    if decision:
        print(decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
