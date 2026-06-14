"""Reconcile service — the sole writer of AcquisitionOutcome (ADR-033 D4/D10)."""

from __future__ import annotations

import contextlib
import json as _json
import logging
from datetime import datetime, timezone

from javdb.pipeline.events import emit as _emit_event  # ADR-036 Phase 2

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
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return 0.0


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
                continue
            # ADR-036 Phase 2: best-effort TorrentCompleted emit (outside the
            # mark_state try/except so a broken emit cannot taint result.errors).
            try:
                _row = r.get(qb_hash)
                _session_id = (_row.session_id or "") if _row else ""
            except Exception:
                _session_id = ""
            with contextlib.suppress(Exception):  # emit is best-effort
                _emit_event(
                    "TorrentCompleted",
                    session_id=_session_id,
                    entity_type="torrent",
                    entity_id=qb_hash,
                    payload=_json.dumps({"completed_at": now}),
                )
    return result


def _fetch_qb_torrents(client, categories) -> list:
    """Fetch qB categories strictly so partial reads cannot drive transitions."""
    get_torrents = getattr(client, "get_torrents", None)
    if callable(get_torrents):
        torrents = []
        for category in categories:
            torrents.extend(get_torrents(category, torrent_filter="all"))
        return torrents
    return client.get_torrents_multiple_categories(list(categories), torrent_filter="all")


def run(options: ReconcileOptions, *, repo=None, qb_client=None) -> ReconcileResult:
    """Reconcile active outcomes against live sources."""
    result = ReconcileResult()
    validation_errors = _validate_options(options)
    if validation_errors:
        result.errors.extend(validation_errors)
        return result

    now = utc_now_iso()
    _client = None  # saved for post-loop missingFiles deletion
    missing_files_hashes: set = set()

    with _repo_ctx(repo) as r:
        active = {rec.qb_hash: rec for rec in r.list_active()}

        observations = {}
        if "qb" in options.sources:
            try:
                _client = qb_client or _build_qb_client()
                torrents = _fetch_qb_torrents(_client, options.categories)
                for t in torrents:
                    if t.get("state") == "missingFiles" and t.get("hash"):
                        missing_files_hashes.add(t["hash"])
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
                if not options.infer_absent:
                    continue
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
                if new_state == "completed":
                    # ADR-036 Phase 2: completions found by the regular reconcile
                    # pass (not just cleanup) also emit TorrentCompleted, so the
                    # shadow projection can track them. Best-effort.
                    with contextlib.suppress(Exception):
                        _emit_event(
                            "TorrentCompleted",
                            session_id=(rec.session_id or ""),
                            entity_type="torrent",
                            entity_id=qb_hash,
                            payload=_json.dumps({"completed_at": now}),
                        )
            except Exception as exc:
                logger.warning("run: upsert failed for %s", qb_hash, exc_info=True)
                result.errors.append(str(exc))

    # Delete missingFiles torrents from qB after all DB writes are committed.
    # Only act on hashes that were actively tracked (present in AcquisitionOutcome)
    # and only when this is a real run (not dry_run).
    if _client is not None and missing_files_hashes and not options.dry_run:
        for qb_hash in missing_files_hashes:
            if qb_hash not in active:
                continue
            try:
                _client.delete_torrents([qb_hash], delete_files=True)
                result.missing_files_deleted += 1
                logger.info("Deleted missingFiles torrent from qB: %s", qb_hash)
            except Exception as exc:
                logger.warning(
                    "run: failed to delete missingFiles torrent %s from qB: %s",
                    qb_hash, exc,
                )
                result.errors.append(str(exc))

    return result


def _build_qb_client():
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


# --- ADR-033 Phase 2: Ownership truth ---------------------------------------

from javdb.ops.reconcile.collectors import (  # noqa: E402
    GdriveOwnershipCollector,
    NasOwnershipCollector,
    PikpakOwnershipCollector,
    QbOwnershipCollector,
)
from javdb.ops.reconcile.models import (  # noqa: E402
    OWNERSHIP_SOURCES,
    PERSISTENT_OWNERSHIP_SOURCES,
    OwnershipLedgerRecord,
    OwnershipOptions,
    OwnershipResult,
)
from javdb.ops.reconcile.persistence import open_ledger_repo  # noqa: E402

# Sources whose snapshots drive a present=0 sweep of absent rows. pikpak is
# monotonic (append-only history); nas is a stub that returns []. Both are
# excluded so an empty/partial snapshot never wipes durable rows (D-P2-5).
_SWEPT_OWNERSHIP_SOURCES = frozenset({"gdrive", "qb"})


@contextlib.contextmanager
def _ledger_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_ledger_repo() as opened:
            yield opened


@contextlib.contextmanager
def _outcome_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_outcome_repo() as opened:
            yield opened


def _load_gdrive_inventory():
    from javdb.storage.repos.operations_repo import OperationsRepo
    from javdb.parsing.common import normalise_code
    from javdb.spider.services.dedup_types import RcloneEntry

    raw = OperationsRepo().load_rclone_inventory()
    inventory: dict = {}
    for code, entries in raw.items():
        ncode = normalise_code(code)
        inventory.setdefault(ncode, []).extend(
            RcloneEntry(
                video_code=normalise_code(e.get("VideoCode", e.get("video_code", ncode))),
                sensor_category=e.get("SensorCategory", e.get("sensor_category", "")),
                subtitle_category=e.get("SubtitleCategory", e.get("subtitle_category", "")),
                folder_path=e.get("FolderPath", e.get("folder_path", "")),
                folder_size=int(e.get("FolderSize", e.get("folder_size", 0)) or 0),
                file_count=int(e.get("FileCount", e.get("file_count", 0)) or 0),
                scan_datetime=e.get("DateTimeScanned", e.get("scan_datetime", "")),
            )
            for e in entries
        )
    return inventory


def _collect_source(source, *, rclone_inventory, qb_outcomes, pikpak_rows):
    if source == "gdrive":
        return GdriveOwnershipCollector().collect(rclone_inventory)
    if source == "qb":
        return QbOwnershipCollector().collect(qb_outcomes)
    if source == "pikpak":
        return PikpakOwnershipCollector().collect(pikpak_rows)
    if source == "nas":
        return NasOwnershipCollector().collect()
    return []


def run_ownership(
    options: OwnershipOptions,
    *,
    repo=None,
    outcome_repo=None,
    rclone_inventory=None,
    qb_outcomes=None,
    pikpak_rows=None,
) -> OwnershipResult:
    """Reconcile OwnershipLedger against all sources. Sole writer of the Ledger."""
    result = OwnershipResult()
    sources = [s for s in options.sources if s in OWNERSHIP_SOURCES]
    if not sources:
        result.errors.append("no valid ownership sources requested")
        return result

    now = utc_now_iso()
    # Lazily load real source data only when a source is requested and no
    # injection was provided (mirrors Phase-1 run()'s lazy qB client build).
    if rclone_inventory is None and "gdrive" in sources:
        rclone_inventory = _load_gdrive_inventory()
    if qb_outcomes is None and "qb" in sources:
        # qb snapshot = outcomes still in the active acquisition pipeline
        # (queued/downloading/completed). Once a video reaches in_library or
        # failed it leaves this snapshot; its qb Ledger row is then swept to
        # present=0 (the qB→archive handoff, D-P2-5).
        with _outcome_ctx(outcome_repo) as o:
            qb_outcomes = [vars(r) for r in o.list_pending_landing()]
    if pikpak_rows is None and "pikpak" in sources:
        from javdb.storage.repos.operations_repo import OperationsRepo
        pikpak_rows = OperationsRepo().load_pikpak_history()

    with _ledger_ctx(repo) as r:
        for source in sources:
            try:
                observations = _collect_source(
                    source,
                    rclone_inventory=rclone_inventory or {},
                    qb_outcomes=qb_outcomes or [],
                    pikpak_rows=pikpak_rows or [],
                )
            except Exception as exc:
                logger.warning("run_ownership: collect failed for %s", source, exc_info=True)
                result.errors.append(str(exc))
                continue
            result.observed += len(observations)
            if options.dry_run:
                continue

            # Delta: load existing rows for this source and only write changes.
            existing = {
                (rec.video_code, rec.category): rec
                for rec in r.list_by_source(source)
            }
            changed: list[OwnershipLedgerRecord] = []
            unchanged_keys: list[tuple[str, str, str]] = []
            present_keys: set[tuple[str, str]] = set()
            for obs in observations:
                present_keys.add((obs.video_code, obs.category))
                new_rec = OwnershipLedgerRecord(
                    video_code=obs.video_code, source=obs.source, category=obs.category,
                    path=obs.path, size=obs.size, present=1, observed_at=now,
                )
                old = existing.get((obs.video_code, obs.category))
                if old is not None and old.present == 1 and old.path == obs.path and old.size == obs.size:
                    unchanged_keys.append((obs.video_code, source, obs.category))
                    continue
                changed.append(new_rec)

            if changed:
                try:
                    result.upserted += r.upsert_batch(changed)
                except Exception as exc:
                    logger.warning("run_ownership: batch upsert failed for %s", source, exc_info=True)
                    result.errors.append(str(exc))

            # Refresh observed_at for unchanged rows (ADR-033 D10 freshness).
            if unchanged_keys:
                try:
                    r.touch_observed_at_batch(unchanged_keys, now)
                except Exception as exc:
                    logger.warning("run_ownership: touch_observed_at failed for %s", source, exc_info=True)
                    result.errors.append(str(exc))

            if source in _SWEPT_OWNERSHIP_SOURCES:
                try:
                    result.swept_absent += r.mark_absent(source, present_keys)
                except Exception as exc:
                    logger.warning("run_ownership: sweep failed for %s", source, exc_info=True)
                    result.errors.append(str(exc))

        # Final step: derive in_library from the now-current persistent sources.
        if options.derive_in_library and not options.dry_run:
            result.marked_in_library += _derive_in_library(r, outcome_repo, now)

    return result


def _derive_in_library(ledger_repo, outcome_repo, now: str) -> int:
    """Promote AcquisitionOutcome rows to in_library when their video_code has a
    present gdrive/nas Ledger entry (D-P2-8). 'failed' rows are left untouched
    (list_pending_landing excludes them).

    Both sides are NFKC+upper normalized before comparison: Ledger gdrive codes
    are stored normalized, but AcquisitionOutcome.video_code is stored verbatim
    from the uploader (e.g. 'n0656', full-width), so a raw compare would never
    match and the row would never land (Codex review on PR #179)."""
    from javdb.parsing.common import normalise_code
    owned = {
        normalise_code(c)
        for c in ledger_repo.list_present_video_codes(PERSISTENT_OWNERSHIP_SOURCES)
    }
    if not owned:
        return 0
    with _outcome_ctx(outcome_repo) as o:
        to_promote = [
            rec.qb_hash
            for rec in o.list_pending_landing()
            if rec.video_code and normalise_code(rec.video_code) in owned
        ]
        if to_promote:
            return o.mark_in_library_batch(to_promote, landed_at=now)
    return 0


# --- ADR-033 Phase 3: Consumption signal ------------------------------------

from javdb.ops.reconcile.code_resolver import resolve_video_code  # noqa: E402
from javdb.ops.reconcile.collectors import MediaServerCollector  # noqa: E402
from javdb.ops.reconcile.models import (  # noqa: E402
    ConsumptionOptions,
    ConsumptionResult,
    ConsumptionSignalRecord,
    UnresolvedMediaItemRecord,
)
from javdb.ops.reconcile.persistence import (  # noqa: E402
    open_consumption_repo,
    open_unresolved_repo,
)

_CONFIDENCE_COUNTER = {
    "high": "resolved_high",
    "medium": "resolved_medium",
    "low": "resolved_low",
}


@contextlib.contextmanager
def _consumption_repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_consumption_repo() as opened:
            yield opened


@contextlib.contextmanager
def _unresolved_repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_unresolved_repo() as opened:
            yield opened


def run_consumption(
    options: ConsumptionOptions,
    *,
    repo=None,
    unresolved_repo=None,
    adapters=None,
) -> ConsumptionResult:
    """Pull watch signal from media servers and UPSERT ConsumptionSignal.

    Sole writer of ConsumptionSignal + UnresolvedMediaItem. Per-instance
    fail-open (ADR-033 D-P3-6): a dead instance logs a masked warning, records
    an error, and is skipped — the pass continues. Prior signal rows for an
    unobserved instance are left untouched."""
    result = ConsumptionResult()
    servers = list(options.servers)
    if not servers:
        logger.info("run_consumption: no MEDIA_SERVERS configured; nothing to do")
        return result

    now = utc_now_iso()
    with _consumption_repo_ctx(repo) as signal_repo, \
            _unresolved_repo_ctx(unresolved_repo) as unresolved:
        for cfg in servers:
            try:
                adapter = (adapters or {}).get(cfg.instance)
                if adapter is None:
                    from javdb.integrations.media_servers import build_adapter
                    adapter = build_adapter(cfg)
                items = MediaServerCollector(adapter).collect(options.since)
            except Exception as exc:
                logger.warning(
                    "run_consumption: instance %s failed (skipping)",
                    cfg.instance,
                    exc_info=True,
                )
                result.errors.append(f"{cfg.instance}: {exc}")
                continue

            result.instances_observed += 1
            result.items_observed += len(items)
            for item in items:
                video_code, confidence = resolve_video_code(item)
                if video_code is None:
                    result.marked_unresolved += 1
                    if not options.dry_run:
                        unresolved.upsert(UnresolvedMediaItemRecord(
                            instance=item.instance,
                            source_type=item.source_type,
                            library_id=item.library_id,
                            library_name=item.library_name,
                            item_id=item.item_id,
                            raw_title=item.title,
                            file_path=item.file_path,
                            observed_at=now,
                        ))
                    continue

                counter = _CONFIDENCE_COUNTER.get(confidence)
                if counter:
                    setattr(result, counter, getattr(result, counter) + 1)
                if options.dry_run:
                    continue
                signal_repo.upsert(ConsumptionSignalRecord(
                    video_code=video_code,
                    source_type=item.source_type,
                    instance=item.instance,
                    library_id=item.library_id,
                    library_name=item.library_name,
                    watched=item.watched,
                    progress_pct=item.progress_pct,
                    play_count=item.play_count,
                    rating=item.rating,
                    watched_at=item.watched_at,
                    resolved_confidence=confidence,
                    observed_at=now,
                ))
                result.signals_updated += 1
                # A server-side item that previously FAILED resolution left a row
                # in UnresolvedMediaItem. Now that it resolves, clear that row or
                # the consumption KPI keeps over-reporting it as unresolved — the
                # two tables share no key, so the read side can't filter it out
                # (Codex review on PR #198). Best-effort: a failed cleanup must
                # never undo the signal write above; deleting an absent row is a
                # no-op. Gated by the dry_run guard above (no writes on dry run).
                try:
                    unresolved.delete(item.instance, item.library_id, item.item_id)
                except Exception:
                    logger.warning(
                        "run_consumption: failed to clear unresolved row %s/%s/%s",
                        item.instance, item.library_id, item.item_id,
                        exc_info=True,
                    )
    return result
