"""Deterministic evidence bundle collection for ADR-026."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from javdb.infra.config import cfg
from javdb.ops.diagnosis.models import EvidenceRef, IncidentBundle


logger = logging.getLogger(__name__)
_ERROR_MARKERS = ("ERROR", "WARNING", "Traceback", "failed", "FAILED", "cancelled")


def _collect_log_snippets(
    paths: Iterable[str | Path],
    *,
    max_lines: int | None = None,
) -> list[str]:
    snippets: list[str] = []
    if max_lines is not None:
        limit = max_lines
    else:
        raw_limit = cfg("OPS_DIAGNOSIS_MAX_LOG_SNIPPETS", 20)
        try:
            limit = int(raw_limit or 20)
        except (TypeError, ValueError):
            logger.warning("Invalid OPS_DIAGNOSIS_MAX_LOG_SNIPPETS=%r; falling back to 20", raw_limit)
            limit = 20
        if limit <= 0:
            logger.warning("Invalid OPS_DIAGNOSIS_MAX_LOG_SNIPPETS=%r; falling back to 20", raw_limit)
            limit = 20
    for raw_path in paths:
        path = Path(raw_path)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if any(marker in line for marker in _ERROR_MARKERS):
                        snippets.append(f"{path.name}: {line[:300]}")
                        if len(snippets) >= limit:
                            return snippets
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.debug("Skipping unreadable ops diagnosis log %s: %s", path, exc)
            continue
    return snippets


def collect_incident_bundle(
    *,
    trigger_source: str,
    run_id: str | None = None,
    run_attempt: int | None = None,
    session_id: str | None = None,
    workflow_name: str | None = None,
    workflow_result: str | None = None,
    session_status: str | None = None,
    drift_verdict: str | None = None,
    recovery_outbox_summary: dict | None = None,
    rollback_safety: str | None = None,
    qb_side_effects: dict | None = None,
    email_summary: str | None = None,
    log_paths: Iterable[str | Path] = (),
) -> IncidentBundle:
    return IncidentBundle(
        trigger_source=trigger_source,
        run_id=run_id,
        run_attempt=run_attempt,
        session_id=session_id,
        workflow_name=workflow_name,
        workflow_result=workflow_result,
        session_status=session_status,
        drift_verdict=drift_verdict,
        recovery_outbox_summary=dict(recovery_outbox_summary or {}),
        rollback_safety=rollback_safety,
        qb_side_effects=dict(qb_side_effects or {}),
        log_snippets=_collect_log_snippets(log_paths),
        email_summary=email_summary,
        runbook_refs=[
            EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/troubleshooting.md"),
            EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/d1-rollback.md"),
        ],
    )
