# javdb/ops/sentinel/service.py
"""Sentinel service — sole writer of fills + drift incidents (ADR-035)."""

from __future__ import annotations

import contextlib
import logging

from javdb.infra.config import cfg
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill, SentinelOptions, SentinelVerdict
from javdb.ops.sentinel.persistence import (
    build_drift_incident, open_fill_repo, open_incident_repo,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _fill_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_fill_repo() as opened:
            yield opened


@contextlib.contextmanager
def _incident_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_incident_repo() as opened:
            yield opened


def _active_session_id() -> str | None:
    try:
        from javdb.storage.db import get_active_session_id
        return get_active_session_id()
    except Exception:
        return None


def persist_run(fills: list[FieldFill], *, session_id: str | None = None, repo=None) -> int:
    sid = session_id or _active_session_id()
    if not sid or not fills:
        return 0
    with _fill_ctx(repo) as r:
        r.upsert_fills(sid, fills)
    return len(fills)


def evaluate_session(
    session_id: str, *, run_id: str | None = None, run_attempt: int | None = None,
    options: SentinelOptions | None = None, fill_repo=None, incident_repo=None,
) -> SentinelVerdict:
    opts = options or SentinelOptions(
        min_sample=int(cfg("SENTINEL_MIN_SAMPLE", 30)),
        baseline_window=int(cfg("SENTINEL_BASELINE_WINDOW", 14)),
    )
    with _fill_ctx(fill_repo) as r:
        fills = r.get_fills(session_id)
        verdict = evaluate(
            fills, min_sample=opts.min_sample,
            baseline_fn=lambda pt, f: r.baseline(pt, f, window=opts.baseline_window),
        )
    if verdict.findings:
        record = build_drift_incident(
            verdict, session_id=session_id, run_id=run_id, run_attempt=run_attempt)
        try:
            with _incident_ctx(incident_repo) as ir:
                ir.upsert(record)
        except Exception:
            logger.warning("evaluate_session: incident persist failed", exc_info=True)
    return verdict


def mark_committed(session_id: str, *, repo=None) -> None:
    with _fill_ctx(repo) as r:
        r.mark_committed(session_id)
