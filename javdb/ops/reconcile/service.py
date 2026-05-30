"""Reconcile service — the sole writer of AcquisitionOutcome (ADR-033 D4/D10)."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timezone

from javdb.integrations.qb.client import extract_hash_from_magnet
from javdb.ops.reconcile.collectors import QbCollector
from javdb.ops.reconcile.models import (
    AcquisitionOutcomeRecord,
    ReconcileOptions,
    ReconcileResult,
    utc_now_iso,
)
from javdb.ops.reconcile.persistence import open_outcome_repo

logger = logging.getLogger(__name__)

_SUPPORTED_SOURCES = frozenset({"qb"})


@contextlib.contextmanager
def _repo_ctx(repo):
    """Use an injected repo as-is, else open the operations DB repo."""
    if repo is not None:
        yield repo
    else:
        with open_outcome_repo() as opened:
            yield opened


def _age_days(iso_ts: str | None) -> float:
    if not iso_ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0


def _validate_options(options: ReconcileOptions) -> list[str]:
    errors: list[str] = []
    sources = tuple(options.sources)
    if not sources:
        errors.append("at least one source is required")
    for source in sorted(set(sources) - _SUPPORTED_SOURCES):
        errors.append(f"unsupported source: {source}")
    if options.stalled_after_days < 1:
        errors.append("stalled_after_days must be >= 1")
    return errors


def record_queued(torrent: dict, session_id: str | None, *, repo=None) -> None:
    """Write a queued row for a torrent just added to qB.

    This is best-effort enrichment: persistence failures are logged and never
    raised, so acquisition outcome tracking cannot break uploaders.
    """
    qb_hash = extract_hash_from_magnet(torrent.get("magnet", ""))
    if not qb_hash:
        logger.debug("record_queued: unparseable magnet, skipping")
        return

    now = utc_now_iso()
    record = AcquisitionOutcomeRecord(
        qb_hash=qb_hash,
        href=torrent.get("href") or "",
        video_code=torrent.get("video_code") or None,
        category=torrent.get("type") or None,
        state="queued",
        queued_at=now,
        last_seen_at=now,
        session_id=session_id,
    )
    try:
        with _repo_ctx(repo) as r:
            r.upsert(record)
    except Exception:
        logger.warning("record_queued: failed to persist queued outcome", exc_info=True)


def apply_cleanup_completed(stats: dict, *, repo=None) -> ReconcileResult:
    """Push completed state for hashes removed by qB cleanup."""
    result = ReconcileResult()
    hashes = [h for h in (stats or {}).get("hashes", []) if h]
    if not hashes:
        return result

    now = utc_now_iso()
    with _repo_ctx(repo) as r:
        for qb_hash in hashes:
            try:
                r.mark_state(qb_hash, "completed", completed_at=now, last_seen_at=now)
                result.marked_completed += 1
            except Exception as exc:
                logger.warning(
                    "apply_cleanup_completed: persist failed for %s",
                    qb_hash,
                    exc_info=True,
                )
                result.errors.append(str(exc))
    return result


def run(options: ReconcileOptions, *, repo=None, qb_client=None) -> ReconcileResult:
    """Reconcile active outcomes against live sources."""
    result = ReconcileResult()
    validation_errors = _validate_options(options)
    if validation_errors:
        result.errors.extend(validation_errors)
        return result

    now = utc_now_iso()
    with _repo_ctx(repo) as r:
        active = {rec.qb_hash: rec for rec in r.list_active()}

        observations = {}
        if "qb" in options.sources:
            try:
                client = qb_client or _build_qb_client(options)
                torrents = client.get_torrents_multiple_categories(
                    list(options.categories), torrent_filter="all"
                )
                for obs in QbCollector().collect(torrents):
                    observations[obs.qb_hash] = obs
            except Exception as exc:
                logger.warning("run: qB read/collect failed, skipping transitions", exc_info=True)
                result.errors.append(str(exc))
                result.observed = 0
                # qB observations are unreliable here, so do not infer absent-state
                # transitions from this pass.
                return result
        result.observed = len(observations)

        for qb_hash, rec in active.items():
            obs = observations.get(qb_hash)
            new_state = None
            extra = {}
            counter_name = None

            if obs is not None:
                if obs.state == "completed" and rec.state != "completed":
                    new_state, extra = "completed", {"completed_at": now}
                    counter_name = "marked_completed"
                elif obs.state == "downloading" and rec.state != "downloading":
                    new_state, extra = "downloading", {}
                    counter_name = "marked_downloading"
                else:
                    new_state, extra = rec.state, {}
                rec.last_seen_at = now
            else:
                age = _age_days(rec.last_seen_at or rec.queued_at)
                if age >= 2 * options.stalled_after_days:
                    new_state, extra = "failed", {}
                    counter_name = "marked_failed"
                elif age >= options.stalled_after_days:
                    new_state, extra = "stalled", {}
                    counter_name = "marked_stalled"
                else:
                    continue

            if new_state is None or options.dry_run:
                continue

            rec.state = new_state
            for attr, value in extra.items():
                setattr(rec, attr, value)
            try:
                r.upsert(rec)
                result.outcomes_updated += 1
                if counter_name is not None:
                    setattr(result, counter_name, getattr(result, counter_name) + 1)
            except Exception as exc:
                logger.warning("run: upsert failed for %s", qb_hash, exc_info=True)
                result.errors.append(str(exc))
    return result


def _build_qb_client(options: ReconcileOptions):
    """Build the real read-only qB client lazily for production runs."""
    from javdb.infra.config import cfg
    from javdb.integrations.qb.client import QBittorrentClient
    from javdb.integrations.qb.config import qb_base_url_candidates

    return QBittorrentClient(
        qb_base_url_candidates(),
        cfg("QB_USERNAME", ""),
        cfg("QB_PASSWORD", ""),
        False,
    )
