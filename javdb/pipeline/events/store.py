"""Best-effort emit + cursor-read for the event spine (ADR-036).

emit() NEVER raises — an event-log failure must not break the pipeline (D4)."""

from __future__ import annotations

import contextlib
import logging

from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with get_db(REPORTS_DB_PATH) as conn:
            yield PipelineEventRepo(conn)


def emit(event_type: str, *, session_id: str, entity_type: str,
         entity_id: str | None = None, payload: str | None = None,
         run_id: str | None = None, run_attempt: int | None = None,
         repo=None) -> int | None:
    if not session_id:
        logger.debug("event emit skipped: missing session_id (type=%s)", event_type)
        return None
    record = PipelineEventRecord(
        event_type=event_type, session_id=session_id, entity_type=entity_type,
        entity_id=entity_id, payload=payload, run_id=run_id, run_attempt=run_attempt,
        created_at=utc_now_iso(),
    )
    try:
        with _repo_ctx(repo) as r:
            return r.append(record)
    except Exception:
        logger.warning("event emit failed (type=%s session=%s)", event_type, session_id, exc_info=True)
        return None


def read_since(last_seq: int, *, limit: int = 500, repo=None) -> list[PipelineEventRecord]:
    with _repo_ctx(repo) as r:
        return r.read_since(last_seq, limit=limit)
