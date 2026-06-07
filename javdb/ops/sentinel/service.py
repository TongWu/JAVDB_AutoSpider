# javdb/ops/sentinel/service.py
"""Sentinel service — sole writer of fills + drift incidents (ADR-035)."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from javdb.infra.config import cfg
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill, SentinelOptions, SentinelVerdict
from javdb.ops.sentinel.persistence import (
    build_drift_incident, open_fill_repo, open_incident_repo,
)

logger = logging.getLogger(__name__)


class CanaryError(RuntimeError):
    """The canary could not complete its job — it fetched/parsed nothing, or it
    detected drift but failed to persist the ``site_drift`` incident.

    Distinct from a clean run (no drift) and from recorded drift: the caller must
    surface it as a non-zero, non-"drift" failure so a scheduled canary run is not
    silently reported as clean when proxy / cookie / CF / JavDB / D1 is broken."""


def _canary_use_proxy() -> bool:
    """Whether the canary's javdb fetch should use the proxy, honouring
    ``PROXY_MODULES`` / ``PROXY_MODE`` (not hard-coded). The canary fetches javdb
    with the same parsers as the spider, so it follows the ``'spider'`` module's
    proxy policy: under the default ``PROXY_MODULES=['spider']`` it proxies, and an
    operator who disables spider proxy (or sets ``PROXY_MODE='none'``) disables it
    here too."""
    from javdb.proxy.policy import should_proxy_module

    return should_proxy_module(
        "spider", None, cfg("PROXY_MODULES", None),
        proxy_mode=cfg("PROXY_MODE", "pool"))


@contextlib.contextmanager
def _fill_ctx(repo: Any) -> Iterator[Any]:
    if repo is not None:
        yield repo
    else:
        with open_fill_repo() as opened:
            yield opened


@contextlib.contextmanager
def _incident_ctx(repo: Any) -> Iterator[Any]:
    if repo is not None:
        yield repo
    else:
        with open_incident_repo() as opened:
            yield opened


def persist_run(fills: list[FieldFill], *, session_id: str | None = None, repo=None) -> int:
    # ADR-046 P5/D2: session is resolved ONLY from the explicit param — the
    # process-global is never consulted. A session-less call (no run yet) is a
    # no-op; callers (run_service) thread the owning session in explicitly.
    sid = session_id
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
                ir.upsert(record.with_persistence_status("d1_written"))
        except Exception:
            logger.warning("evaluate_session: incident persist failed", exc_info=True)
    return verdict


def mark_committed(session_id: str, *, repo=None) -> None:
    with _fill_ctx(repo) as r:
        r.mark_committed(session_id)


def run_canary(
    *, gateway=None, options: SentinelOptions | None = None,
    run_id: str | None = None, run_attempt: int | None = None,
    fill_repo=None, incident_repo=None,
) -> SentinelVerdict:
    """Independent between-run canary (ADR-035 D1 source b).

    Fetch the pinned pages, evaluate index fill-rate against the committed daily
    baseline (reusing the Phase-1 detector core), merge golden-anchor findings,
    and emit one ``site_drift`` incident (trigger_source='canary') on any finding.
    Read-only w.r.t. ``ParseRunFieldFill``; the service is the sole incident writer."""
    from javdb.ops.sentinel import probes  # local import: avoids a spider import at module load

    opts = options or SentinelOptions(
        min_sample=int(cfg("SENTINEL_MIN_SAMPLE", 30)),
        baseline_window=int(cfg("SENTINEL_BASELINE_WINDOW", 14)),
    )
    if gateway is None:
        from javdb.spider.spider_gateway import create_gateway
        gateway = create_gateway(
            use_proxy=_canary_use_proxy(), use_cf_bypass=True, use_cookie=True)

    obs = probes.run_probes(gateway)

    # Total availability failure: not a single pinned page was fetched+parsed.
    # Per ADR-035 D4 this is NOT site drift (no incident is raised), but it must
    # not be reported as a clean run — a dead proxy / expired cookie / CF wall /
    # unreachable JavDB would otherwise let the canary silently flatline.
    if obs.fetch_failures and not obs.fills and not obs.anchor_findings:
        raise CanaryError(
            f"canary fetched/parsed nothing ({len(obs.fetch_failures)} fetch "
            f"failure(s)): {obs.fetch_failures}")

    # Fill-rate findings via the shared core; baseline = committed daily runs.
    with _fill_ctx(fill_repo) as r:
        verdict = evaluate(
            obs.fills, min_sample=opts.min_sample,
            baseline_fn=lambda pt, f: r.baseline(pt, f, window=opts.baseline_window),
        )

    # Merge golden-anchor findings (a known page parsed wrong -> critical).
    for finding in obs.anchor_findings:
        verdict.findings.append(finding)
        if finding.severity == "critical":
            verdict.critical = True

    if verdict.findings:
        record = build_drift_incident(
            verdict, session_id=None, run_id=run_id, run_attempt=run_attempt,
            trigger_source="canary")
        try:
            with _incident_ctx(incident_repo) as ir:
                ir.upsert(record.with_persistence_status("d1_written"))
        except Exception as exc:
            # Unlike the daily gate (which fails OPEN so a sentinel bug can't halt
            # the pipeline), the canary is a standalone cron run: if it found drift
            # but cannot record the incident, returning a "success" exit would hide
            # real drift. Fail loudly so the workflow surfaces it.
            raise CanaryError(
                "canary detected drift but failed to persist the site_drift "
                "incident") from exc
    return verdict
