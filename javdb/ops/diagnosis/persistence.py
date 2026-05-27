"""D1-first incident persistence with JSONL fallback."""

from __future__ import annotations

import logging
from pathlib import Path

from javdb.infra.logging import log_section, log_summary_block
from javdb.ops.diagnosis.jsonl_store import append_incident_jsonl
from javdb.ops.diagnosis.models import OpsIncidentRecord
from javdb.storage.db import get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


logger = logging.getLogger(__name__)


def persist_incident(
    record: OpsIncidentRecord,
    *,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
) -> OpsIncidentRecord:
    d1_record = record.with_persistence_status("d1_written")
    try:
        if repo is not None:
            repo.upsert(d1_record)
        else:
            with get_db("reports") as conn:
                OpsIncidentRepo(conn).upsert(d1_record)
        return d1_record
    except Exception:
        log_section(logger, "D1 incident persistence failed")
        log_summary_block(
            logger,
            "Ops diagnosis JSONL fallback",
            {
                "Incident": record.incident_id,
                "Fallback": str(jsonl_path or "default reports/ops/ops_incidents.jsonl"),
            },
        )
        logger.exception(
            "Failed to persist ops incident; falling back to JSONL: incident_id=%s jsonl_path=%s",
            record.incident_id,
            jsonl_path,
        )
        fallback_record = record.with_persistence_status("d1_failed_jsonl_written")
        append_incident_jsonl(fallback_record, jsonl_path)
        return fallback_record
