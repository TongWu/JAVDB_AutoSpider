"""Shared scaffolding for session lifecycle CLIs (``apps.cli.db.rollback`` and ``apps.cli.db.commit_session``).

Both CLIs mutate ``ReportSessions`` lifecycle state. They share these
mechanics:

* ISO timestamp normalisation (``--run-started-at``).
* Session lookups by ``(run_id, attempt)`` or ``DateTimeCreated`` window.
* Pre-state read (``write_mode``, ``status``) before any mutation.
* MovieClaim DO fan-out — rollback drops staged movies, commit promotes
  them. The iteration / error-handling / client lifecycle is identical;
  only the underlying method call differs.
* ``pending_session_verify`` / ``rollback_summary`` JSONL emission for
  the email pipeline and Phase 3 health rollup.
* ``GITHUB_OUTPUT`` KV writes for downstream workflow steps.

The two CLIs differ in argparse flags, the per-session operation, the
cross-day sanity check (rollback only), shadow-audit drift comparison
(commit only) and exit-code semantics. Those differences stay in each
CLI's ``main()`` flow; only the truly shared mechanics live here.

**Historical note** — ``commit_session.py`` previously carried an
inline ``_normalize_run_started_at`` whose docstring claimed parity
with ``rollback.py`` but in fact diverged: the rollback version used
``datetime.fromisoformat`` (which correctly converts non-UTC offsets
to naive UTC), while the commit version did ad-hoc string slicing
that left non-UTC inputs at their local wall-clock value. GitHub
Actions only emits ``Z``-suffixed timestamps so the divergence never
bit production, but it was a latent defect. The canonical
implementation here is the ``fromisoformat`` form.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from javdb.storage.db import (
    db_find_in_progress_sessions,
    db_find_sessions_by_run,
    db_get_session_run_identity,
    db_get_session_status,
)
from javdb.infra.logging import get_logger
from javdb.proxy.coordinator.movie_claim_client import (
    MovieClaimUnavailable,
    create_movie_claim_client_from_env,
    current_shard_date,
)


logger = get_logger(__name__)


# ── Timestamp normalisation (canonical, replaces two divergent copies) ──


def normalize_run_started_at(raw: Optional[str]) -> Optional[str]:
    """Convert an ISO timestamp into the SQLite-friendly UTC form.

    GitHub passes timestamps like ``2026-05-04T19:30:00Z``;
    ``ReportSessions.DateTimeCreated`` stores UTC as
    ``2026-05-04 19:30:00``. Offset-aware inputs (``+08:00`` etc.) are
    converted to UTC before being stripped of their tzinfo so the
    resulting string is lexicographically comparable against the
    naive-UTC values in the DB.

    Returns ``None`` for falsy input or anything ``fromisoformat`` can't
    parse. Callers must treat ``None`` as "no timestamp supplied" and
    refuse window-scan fallbacks rather than scan unbounded.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Session lookups ────────────────────────────────────────────────────


def find_run_sessions(
    run_id: str, attempt: Optional[int],
) -> List[str]:
    """Wrap ``db_find_sessions_by_run`` with the warn-and-return-empty
    error handling both CLIs use. Returns an empty list on any failure.
    """
    try:
        return list(db_find_sessions_by_run(run_id, attempt))
    except Exception as exc:  # noqa: BLE001 — best-effort lookup
        logger.warning(
            "db_find_sessions_by_run(run_id=%s attempt=%s) failed: %s",
            run_id, attempt, exc,
        )
        return []


def find_window_sessions(
    since: Optional[str], *, raise_on_error: bool = False,
) -> List[str]:
    """Wrap ``db_find_in_progress_sessions``. ``since`` is expected to
    be a normalised string (see :func:`normalize_run_started_at`).

    Error handling is **opt-in** because the two CLIs disagree on what a
    DB lookup failure means:

    * ``raise_on_error=False`` (default) — warn-and-empty. The caller
      treats an empty list as "no targets" and continues with whatever
      it already has. ``commit_session`` and any best-effort discovery
      use this mode.
    * ``raise_on_error=True`` — let the exception propagate. The
      ``rollback`` CLI uses this so a transient DB error in the window
      scan still produces its documented ``exit 3`` instead of being
      silently downgraded to a successful no-op cleanup.
    """
    if not since:
        return []
    try:
        return list(db_find_in_progress_sessions(since=since))
    except Exception as exc:  # noqa: BLE001
        if raise_on_error:
            raise
        logger.warning(
            "db_find_in_progress_sessions(since=%s) failed: %s",
            since, exc,
        )
        return []


# ── Pre-state read ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionPreState:
    """Snapshot of ``(WriteMode, Status)`` captured before any mutation.

    Both CLIs need this before they touch a session: the rollback CLI
    so it can decide whether to emit the pending-verify line (only
    pending sessions count); the commit CLI so it can route pending
    sessions through ``db_commit_session_history`` before the status
    flip. ``write_mode`` defaults to ``'audit'`` when the lookup fails,
    matching the historical fallback in both CLIs.
    """

    write_mode: str
    status: Optional[str]


def read_session_pre_state(session_id: str) -> SessionPreState:
    """Best-effort read of ``(WriteMode, Status)`` for *session_id*.

    Failures fall back to ``write_mode='audit'`` / ``status=None`` —
    matches the historical "treat unknown as legacy audit path" stance
    in both CLIs.
    """
    try:
        state = db_get_session_status(session_id)
    except Exception as exc:  # noqa: BLE001 — never block mutation
        logger.warning(
            "db_get_session_status(%s) failed: %s; "
            "falling back to write_mode='audit'", session_id, exc,
        )
        return SessionPreState(write_mode="audit", status=None)
    if not state:
        return SessionPreState(write_mode="audit", status=None)
    return SessionPreState(write_mode=state[0], status=state[1])


# ── MovieClaim DO fan-out ──────────────────────────────────────────────


@dataclass(frozen=True)
class _FanoutConfig:
    method_name: str          # 'rollback_staged_movies' / 'commit_completed_movies'
    count_key: str            # 'removed' / 'promoted'
    log_verb: str             # 'rollback' / 'commit'
    log_count_label: str      # 'removed=%s' / 'promoted=%s'


_FANOUT_CONFIGS = {
    "rollback": _FanoutConfig(
        method_name="rollback_staged_movies",
        count_key="removed",
        log_verb="rollback",
        log_count_label="removed",
    ),
    "commit": _FanoutConfig(
        method_name="commit_completed_movies",
        count_key="promoted",
        log_verb="commit",
        log_count_label="promoted",
    ),
}


def fanout_movie_claim(
    session_ids: Sequence[str],
    *,
    operation: str,                     # 'rollback' | 'commit'
    shard_date: Optional[str] = None,
    max_attempts: int = 1,
) -> List[dict]:
    """Iterate sessions and call the MovieClaim coordinator.

    ``operation='rollback'`` calls ``rollback_staged_movies`` with
    exponential-backoff retries (1s, 2s, 4s, ...). ``operation='commit'``
    calls ``commit_completed_movies`` once per session (no retry — the
    StaleSessionCleanup orphan sweep reconciles failures within 48h).

    Best-effort: a coordinator outage MUST NOT block the DB-side
    mutation that already happened. Returns one summary dict per
    session, with the operation-specific count key (``removed`` for
    rollback, ``promoted`` for commit) and an ``ok`` flag.
    """
    if not session_ids:
        return []
    if operation not in _FANOUT_CONFIGS:
        raise ValueError(
            f"unknown operation {operation!r}; "
            f"expected one of {sorted(_FANOUT_CONFIGS)}"
        )
    config = _FANOUT_CONFIGS[operation]

    client = create_movie_claim_client_from_env()
    if client is None:
        logger.info(
            "MovieClaim coordinator not configured — skipping %s "
            "(DB-side mutation unaffected)", config.method_name,
        )
        return []
    attempts = max(1, int(max_attempts))
    target_date = shard_date or current_shard_date()
    summaries: List[dict] = []
    try:
        for sid in session_ids:
            count: Optional[int] = None
            last_error: Optional[str] = None
            for attempt_idx in range(1, attempts + 1):
                try:
                    method = getattr(client, config.method_name)
                    result = method(str(sid), date=target_date)
                    count = getattr(result, config.count_key)
                    last_error = None
                    break
                except MovieClaimUnavailable as exc:
                    last_error = str(exc)
                    logger.warning(
                        "MovieClaim %s attempt %d/%d failed for "
                        "session=%s shard=%s: %s",
                        config.log_verb, attempt_idx, attempts,
                        sid, target_date, exc,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.warning(
                        "Unexpected MovieClaim %s error attempt %d/%d "
                        "for session=%s shard=%s",
                        config.log_verb, attempt_idx, attempts,
                        sid, target_date, exc_info=True,
                    )
                if attempt_idx < attempts:
                    time.sleep(2 ** (attempt_idx - 1))

            base: Dict[str, Any] = {
                "session_id": sid,
                "shard_date": target_date,
            }
            if count is None:
                logger.error(
                    "MovieClaim %s gave up for session=%s shard=%s "
                    "after %d attempt(s) — orphan sweep will reconcile",
                    config.log_verb, sid, target_date, attempts,
                )
                base[config.count_key] = 0
                base["ok"] = False
                base["error"] = last_error or "unknown"
                if attempts > 1:
                    base["attempts"] = attempts
            else:
                logger.info(
                    "MovieClaim %s: session=%s shard=%s %s=%s",
                    config.log_verb, sid, target_date,
                    config.log_count_label, count,
                )
                base[config.count_key] = count
                base["ok"] = True
            summaries.append(base)
    finally:
        client.close()
    return summaries


# ── Metric emission ────────────────────────────────────────────────────


def append_jsonl_record(
    record: dict,
    *,
    reports_dir: Optional[str] = None,
    filename: str = "d1_drift.jsonl",
) -> None:
    """Append *record* as one JSON line to ``<reports_dir>/D1/<filename>``.

    ``reports_dir`` defaults to ``$REPORTS_DIR`` or ``reports``. The
    function never raises: metric emission must not block the primary
    operation. Any failure is logged at WARNING and discarded.
    """
    try:
        base = reports_dir or os.environ.get("REPORTS_DIR", "reports")
        path = os.path.join(base, "D1", filename)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to append metric record to %s: %s", filename, exc,
        )


def write_github_output(**kvpairs: Any) -> None:
    """Append ``key=value`` lines to ``$GITHUB_OUTPUT`` if set.

    Silent no-op when the env var is missing (typical local CLI run).
    Best-effort: failures are logged but never raise.
    """
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return
    try:
        with open(gh_output, "a", encoding="utf-8") as f:
            for key, value in kvpairs.items():
                f.write(f"{key}={value}\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write GITHUB_OUTPUT: %s", exc)


# ── Run identity enrichment ────────────────────────────────────────────


def attach_run_identity(record: dict, session_id: str) -> None:
    """Best-effort attach of ``run_id`` / ``run_attempt`` keys to *record*.

    Mutates the record in place. Silently no-op when the lookup fails
    or returns no identity — both CLIs already tolerate this case.
    """
    try:
        identity = db_get_session_run_identity(session_id)
    except Exception:  # noqa: BLE001
        return
    if identity is None:
        return
    record["run_id"] = identity[0]
    record["run_attempt"] = identity[1]
