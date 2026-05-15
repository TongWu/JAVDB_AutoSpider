"""HTTP client for the Cloudflare Worker + Durable Object runner registry.

Sister to :mod:`packages.python.javdb_platform.proxy_coordinator_client`,
:mod:`packages.python.javdb_platform.login_state_client`, and
:mod:`packages.python.javdb_platform.movie_claim_client`; targets the
``RunnerRegistry`` singleton DO (P2-E).

Two purposes (intentionally folded into one DO so we don't pay for a
second binding):

1. **Operational visibility** — every spider runner registers itself at
   startup, sends a heartbeat every 60 s, and unregisters on shutdown.
   Ops can poll ``GET /active_runners`` to answer "how many runners are
   live, what workflows are they part of, when did they last heartbeat?"
   without crawling GH Actions logs.

2. **Configuration drift detection** (subsumes the original P3-B
   plan item) — every register payload carries
   ``proxy_pool_hash = sha1(PROXY_POOL_JSON)[:16]``, and the response
   surfaces the live distribution of hashes so a freshly-joining runner
   can ``WARN`` when its hash is in the minority bucket.  This catches
   silent ``PROXY_POOL_JSON`` drift across runners (e.g. one workflow
   was redeployed with a new pool, the others weren't) at the moment
   of the next register, not only after a failure surfaces the issue.

Design constraints (mirror those of the sibling clients):

- One HTTP round-trip per call; **no retries** on failure.  Callers
  treat :class:`RunnerRegistryUnavailable` as a fail-open signal and
  continue without any registry coordination.  Registry outages MUST
  NOT impact spider correctness — the registry is purely operational
  metadata.
- Short timeout (default 5 s) bounds the worst-case impact of a slow
  Worker; the spider's startup path is never blocked for more than that.
- Thread-safe: each call constructs its own request and the underlying
  ``requests.Session`` only stores connection-pool metadata.

Wire format and field semantics live in
``JAVDB_AutoSpider_Proxycoordinator/src/types.ts`` (the
``RunnerInfo`` / ``RegisterRunnerRequest`` cluster).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from packages.python.javdb_platform.do_client_base import (
    BaseDOClient,
    DOClientUnavailable,
)
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-runner-registry-client/1.0"


def proxy_pool_hash(proxy_pool_json: str) -> str:
    """Compute the 16-hex-char hash used for cross-runner drift detection.

    Mirrors the Worker-side semantics: ``sha1`` of the canonical JSON
    representation, truncated to the first 16 hex characters (64 bits).
    Truncation keeps the hash short enough to log compactly while still
    leaving 2^32 collision-resistance for the realistic case of
    O(N) live runners.

    Empty input still maps to the empty hash ("no hash"). Invalid JSON is
    hashed from its raw bytes and should be treated as a valid drift hash.
    """
    if not proxy_pool_json or not proxy_pool_json.strip():
        return ""
    try:
        # Re-encode in canonical form so cosmetic differences (key order,
        # whitespace, trailing newline) don't show up as a fake "drift".
        normalized = json.dumps(
            json.loads(proxy_pool_json),
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        # Caller passed something that looked like JSON but isn't —
        # still hash the raw bytes so a mismatch is at least detectable,
        # rather than collapsing to "" and silencing all drift.
        normalized = proxy_pool_json
    return hashlib.sha1(
        normalized.encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]


class RunnerRegistryUnavailable(DOClientUnavailable):
    """Raised when the runner-registry Worker cannot be reached or returns an error.

    Pure signal, never a panic.  Every callsite in the spider treats it
    as "fall back to no registry" — registration / heartbeat / unregister
    each become silent no-ops, matching the pre-P2-E world exactly.
    """


@dataclass(frozen=True)
class RunnerInfo:
    """One row of the registry, describing a single live spider runner.

    Returned in :class:`RegisterResult.active_runners` /
    :class:`ActiveRunnersResult.active_runners`.  Read-only metadata.
    """

    holder_id: str
    workflow_run_id: str
    workflow_name: str
    started_at: int
    last_heartbeat: int
    proxy_pool_hash: str
    page_range: Optional[str]


@dataclass(frozen=True)
class PoolHashBucket:
    """Aggregated occurrence count of one ``proxy_pool_hash`` in the live registry."""

    hash: str
    count: int


@dataclass(frozen=True)
class Signal:
    """W5.4 — operator-pushed active signal.

    Mirrors the Worker-side ``Signal`` type. ``kind`` is one of
    ``throttle_global`` / ``ban_proxy`` / ``pause_all`` / ``resume``.
    Time-bounded via ``expires_at_ms`` (Worker GC + read-time filter
    drop expired entries automatically).

    The Python client parses these but does NOT yet apply them — the
    consumer integration with :class:`MovieSleepManager` and
    :class:`ProxyPool` is deferred to a follow-up. Until then this
    surface lets the heartbeat loop expose the signal list for ops
    visibility without coupling to specific consumers.
    """

    id: str
    kind: str
    expires_at_ms: int
    created_at_ms: int
    reason: Optional[str] = None
    factor: Optional[float] = None
    proxy_id: Optional[str] = None


_VALID_SIGNAL_KINDS = frozenset(
    {"throttle_global", "ban_proxy", "pause_all", "resume"}
)


def _parse_signal(payload: Any) -> Optional[Signal]:
    """Decode one signal entry from a wire payload, returning ``None`` on
    any structural / type error. Fail-open: bad signals are dropped, not
    raised. Coerces numeric / string fields defensively so a Worker that
    sends a number where a string is expected (or vice versa) doesn't
    crash the heartbeat parser."""
    if not isinstance(payload, dict):
        return None
    try:
        kind = str(payload.get("kind", ""))
        if kind not in _VALID_SIGNAL_KINDS:
            return None
        sig_id = str(payload.get("id", ""))
        if not sig_id:
            return None
        expires = int(payload.get("expires_at_ms", 0) or 0)
        created = int(payload.get("created_at_ms", 0) or 0)
        reason_raw = payload.get("reason")
        reason = str(reason_raw) if reason_raw not in (None, "") else None
        factor_raw = payload.get("factor")
        factor = float(factor_raw) if factor_raw is not None else None
        proxy_id_raw = payload.get("proxy_id")
        proxy_id = (
            str(proxy_id_raw)
            if proxy_id_raw not in (None, "")
            else None
        )
        return Signal(
            id=sig_id,
            kind=kind,
            expires_at_ms=expires,
            created_at_ms=created,
            reason=reason,
            factor=factor,
            proxy_id=proxy_id,
        )
    except (TypeError, ValueError):
        return None


def _parse_signal_list(payload: Any) -> List[Signal]:
    """Decode a list of signals from a wire payload. Always returns a
    list (possibly empty); never raises. Drops individual malformed
    entries silently so one bad signal doesn't poison the rest."""
    if payload is None:
        return []
    if not isinstance(payload, list):
        return []
    out: List[Signal] = []
    for entry in payload:
        sig = _parse_signal(entry)
        if sig is not None:
            out.append(sig)
    return out


@dataclass(frozen=True)
class ConfigSnapshot:
    """W5.3 — versioned snapshot of operator-tunable runtime config.

    Surfaced by the Worker on every ``/register`` and ``/heartbeat``
    response (when the v4 migration is applied). ``version`` increments
    monotonically on every successful ``PATCH /config``; clients use it
    to detect changes between heartbeats without diffing ``values``.

    ``values`` is a partial map of operator-set overrides. Keys not
    present in ``values`` fall back to the Worker's env-var defaults —
    so a fresh deployment with no PATCH applied returns
    ``ConfigSnapshot(version=0, values={})``.

    Treated as opaque by old Python clients (the field defaults to
    ``None`` when the Worker is on a pre-W5.3 deploy that omits it).
    """

    version: int = 0
    updated_at_ms: int = 0
    values: Dict[str, str] = field(default_factory=dict)


def _parse_config_snapshot(payload: Any) -> Optional[ConfigSnapshot]:
    """Decode the ``config`` field embedded in register/heartbeat replies.

    Returns ``None`` when the Worker omits the field (forward-compat
    with pre-W5.3 Workers) or sends a structurally invalid payload —
    the Python client treats either case as "no config override, use
    local defaults" and continues, never raising. This mirrors the
    "fail-open on telemetry" contract of the rest of this client.
    """
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        raw_values = payload.get("values", {}) or {}
        if not isinstance(raw_values, dict):
            return None
        # Coerce values to str on read so a Worker that ever returns a
        # number doesn't crash the dataclass freeze.
        values: Dict[str, str] = {
            str(k): str(v) for k, v in raw_values.items()
        }
        return ConfigSnapshot(
            version=int(payload.get("version", 0) or 0),
            updated_at_ms=int(payload.get("updated_at", 0) or 0),
            values=values,
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RegisterResult:
    """Reply from ``POST /register``.

    ``registered`` is ``False`` when the same ``holder_id`` was already
    in the registry (the call is treated as an implicit heartbeat +
    metadata refresh).  Two-runner deployments that re-register on a
    transient network blip rely on this idempotency.

    ``active_runners`` and ``pool_hash_summary`` always reflect the live
    state *after* this register — so a freshly-joining runner can warn
    about drift from its very first registry call without an extra GET.

    ``movie_claim_recommended`` is the Worker's derived signal for whether
    the per-day MovieClaim mutex should currently be active (= the live
    cohort has reached ``movie_claim_min_runners``).  Defaults to
    ``False`` / ``0`` for forward compatibility with older Workers that
    don't ship the field — matching the safe "single-runner" semantics
    the auto-toggle uses to keep claim coordination off.

    ``config`` is the W5.3 dynamic-config snapshot; ``None`` when the
    Worker is on a pre-W5.3 deploy. Consumers fall back to env-var
    defaults in that case.
    """

    registered: bool
    active_runners: List[RunnerInfo] = field(default_factory=list)
    pool_hash_summary: List[PoolHashBucket] = field(default_factory=list)
    server_time_ms: int = 0
    movie_claim_recommended: bool = False
    movie_claim_min_runners: int = 0
    config: Optional[ConfigSnapshot] = None
    #: W5.4 — operator-pushed active signals (always a list, possibly empty;
    #: never ``None`` so call-sites don't need to guard).
    active_signals: List[Signal] = field(default_factory=list)


@dataclass(frozen=True)
class HeartbeatResult:
    """Reply from ``POST /heartbeat``.

    ``alive=False`` is returned for an unknown ``holder_id`` (e.g. the
    GC alarm pruned it because heartbeats lapsed).  The client should
    re-register on ``alive=False`` rather than treating it as fatal —
    the registry is designed so a transient outage automatically heals
    without operator intervention.

    ``movie_claim_recommended`` mirrors the field on
    :class:`RegisterResult` so the heartbeat loop can feed cohort
    changes (e.g. a peer's atexit ``unregister`` arriving) into
    ``state._apply_movie_claim_recommendation`` without an extra
    register round-trip.  Defaults to ``False`` / ``0`` for old Workers.

    ``config`` mirrors the field on :class:`RegisterResult`: long-running
    runners pick up operator PATCHes within one heartbeat interval
    without an explicit ``GET /config`` round-trip. ``None`` on pre-W5.3
    Workers.
    """

    alive: bool
    server_time_ms: int
    movie_claim_recommended: bool = False
    movie_claim_min_runners: int = 0
    active_runners_count: int = 0
    config: Optional[ConfigSnapshot] = None
    #: W5.4 — see :attr:`RegisterResult.active_signals`.
    active_signals: List[Signal] = field(default_factory=list)


@dataclass(frozen=True)
class UnregisterResult:
    """Reply from ``POST /unregister``.

    ``unregistered=False`` for an unknown holder is silent no-op
    (matches ``release_lease`` semantics).  Atexit + signal handlers
    can both call ``unregister`` without coordination.
    """

    unregistered: bool
    server_time_ms: int


@dataclass(frozen=True)
class ActiveRunnersResult:
    """Reply from ``GET /active_runners``.

    Read-only snapshot for ops dashboards; the call does NOT update
    ``last_heartbeat`` so polling at high cadence won't keep idle
    runners alive past their stale TTL.
    """

    active_runners: List[RunnerInfo]
    pool_hash_summary: List[PoolHashBucket]
    server_time_ms: int


def _extract_server_time_ms(data: dict) -> int:
    """Read the server-side timestamp, preferring an explicit-units key.

    Module-level alias of :meth:`BaseDOClient._extract_server_time_ms`
    so existing tests / call-sites keep importing it from this module
    unchanged.
    """
    return BaseDOClient._extract_server_time_ms(data)


def _strict_bool(value) -> bool:
    return value is True


def _parse_runner_info(payload: Any) -> RunnerInfo:
    """Decode one ``RunnerInfo`` JSON row into the dataclass."""
    if not isinstance(payload, dict):
        raise ValueError(f"runner entry must be an object, got {type(payload).__name__}")
    page_range = payload.get("page_range", None)
    if page_range is not None and not isinstance(page_range, str):
        raise ValueError("runner entry page_range must be a string or null")
    return RunnerInfo(
        holder_id=str(payload.get("holder_id", "") or ""),
        workflow_run_id=str(payload.get("workflow_run_id", "") or ""),
        workflow_name=str(payload.get("workflow_name", "") or ""),
        started_at=int(payload.get("started_at", 0) or 0),
        last_heartbeat=int(payload.get("last_heartbeat", 0) or 0),
        proxy_pool_hash=str(payload.get("proxy_pool_hash", "") or ""),
        page_range=str(page_range) if page_range else None,
    )


def _parse_runner_list(payload: Any) -> List[RunnerInfo]:
    """Decode ``active_runners`` while rejecting malformed list entries."""
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("active_runners must be a list")
    return [_parse_runner_info(r) for r in payload]


def _parse_hash_summary(payload: Any) -> List[PoolHashBucket]:
    """Decode the ``pool_hash_summary`` array into typed buckets."""
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("pool_hash_summary must be a list")
    out: List[PoolHashBucket] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("pool_hash_summary entries must be objects")
        out.append(PoolHashBucket(
            hash=str(entry.get("hash", "") or ""),
            count=int(entry.get("count", 0) or 0),
        ))
    return out


class RunnerRegistryClient(BaseDOClient):
    """HTTP client for the RunnerRegistry DO.

    Construct once per process and pass into the runtime's startup +
    heartbeat daemons.  All four methods are blocking and short-lived;
    the spider's hot path is bounded by ``timeout`` (5 s default) on
    every call.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``
            (same Worker that hosts the per-proxy throttle + login state +
            movie-claim endpoints).
        token: Bearer token (must match the secret set via
            ``wrangler secret put PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    _unavailable_exc = RunnerRegistryUnavailable

    # -- public API ---------------------------------------------------------

    def register(
        self,
        *,
        holder_id: str,
        workflow_run_id: str = "",
        workflow_name: str = "",
        started_at: Optional[int] = None,
        proxy_hash: str = "",
        page_range: Optional[str] = None,
    ) -> RegisterResult:
        """Register *this* runner with the singleton registry.

        Idempotent on ``holder_id`` — repeated calls preserve
        ``started_at`` from the first registration and refresh
        ``last_heartbeat``.  This lets a defensive client re-register
        after a network partition without polluting "uptime" telemetry.

        The request's ``proxy_pool_hash`` field powers the cross-runner
        drift check; pass :func:`proxy_pool_hash` of the runner's
        ``PROXY_POOL_JSON`` so peers see a consistent hash across
        logically-equivalent JSON.

        Raises :class:`ValueError` for invalid caller input and
        :class:`RunnerRegistryUnavailable` on registry failures
        (timeout, non-2xx, malformed response).
        """
        if not isinstance(holder_id, str) or not holder_id:
            raise ValueError("holder_id must be a non-empty string")
        body = {
            "holder_id": holder_id,
            "workflow_run_id": workflow_run_id,
            "workflow_name": workflow_name,
            "proxy_pool_hash": proxy_hash,
            "page_range": page_range,
        }
        if started_at is not None:
            body["started_at"] = int(started_at)
        resp = self._do_request("POST", "/register", body)
        try:
            return RegisterResult(
                registered=_strict_bool(resp.get("registered")),
                active_runners=_parse_runner_list(resp.get("active_runners", [])),
                pool_hash_summary=_parse_hash_summary(resp.get("pool_hash_summary", []) or []),
                server_time_ms=_extract_server_time_ms(resp),
                # Auto-toggle signals (added with the per-day MovieClaim
                # auto-mount feature).  Missing keys are NOT treated as
                # malformed: an older Worker that predates the contract
                # extension simply returns the safe "single-runner"
                # default, which `state._apply_movie_claim_recommendation`
                # interprets as "do not mount".
                movie_claim_recommended=_strict_bool(
                    resp.get("movie_claim_recommended")
                ),
                movie_claim_min_runners=int(
                    resp.get("movie_claim_min_runners", 0) or 0
                ),
                # W5.3 — dynamic-config snapshot; ``None`` on pre-v4 Workers.
                config=_parse_config_snapshot(resp.get("config")),
                # W5.4 — operator signals; empty list on pre-W5.4 Workers.
                active_signals=_parse_signal_list(resp.get("active_signals")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise RunnerRegistryUnavailable(
                f"malformed register response: {resp!r} ({e})"
            ) from e

    def heartbeat(self, holder_id: str) -> HeartbeatResult:
        """Refresh ``last_heartbeat`` for *holder_id*.

        Designed to run on a 60 s daemon loop.  Returns
        ``alive=False`` (not an exception) when the registry has
        evicted the holder — the daemon should re-register and keep
        running rather than treating eviction as a crash.
        """
        if not isinstance(holder_id, str) or not holder_id:
            raise ValueError("holder_id must be a non-empty string")
        resp = self._do_request("POST", "/heartbeat", {"holder_id": holder_id})
        try:
            return HeartbeatResult(
                alive=_strict_bool(resp.get("alive")),
                server_time_ms=_extract_server_time_ms(resp),
                # Same forward-compat defaults as `register`: missing keys
                # collapse to "single-runner safe", letting old Workers
                # coexist with new clients without spurious mount/unmount
                # churn.  See `RegisterResult` docstring.
                movie_claim_recommended=_strict_bool(
                    resp.get("movie_claim_recommended")
                ),
                movie_claim_min_runners=int(
                    resp.get("movie_claim_min_runners", 0) or 0
                ),
                active_runners_count=int(
                    resp.get("active_runners_count", 0) or 0
                ),
                # W5.3 — dynamic-config snapshot; ``None`` on pre-v4 Workers.
                config=_parse_config_snapshot(resp.get("config")),
                # W5.4 — operator signals; empty list on pre-W5.4 Workers.
                active_signals=_parse_signal_list(resp.get("active_signals")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise RunnerRegistryUnavailable(
                f"malformed heartbeat response: {resp!r} ({e})"
            ) from e

    def unregister(self, holder_id: str) -> UnregisterResult:
        """Remove *holder_id* from the registry (atexit / signal handler).

        Idempotent — calling for an unknown holder returns
        ``unregistered=False`` without raising.  Both ``atexit`` and
        signal-driven shutdown handlers can call this without
        coordination.
        """
        if not isinstance(holder_id, str) or not holder_id:
            raise ValueError("holder_id must be a non-empty string")
        resp = self._do_request("POST", "/unregister", {"holder_id": holder_id})
        try:
            return UnregisterResult(
                unregistered=_strict_bool(resp.get("unregistered")),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise RunnerRegistryUnavailable(
                f"malformed unregister response: {resp!r} ({e})"
            ) from e

    def get_active_runners(self) -> ActiveRunnersResult:
        """Read-only snapshot of all live runners.  Ops / debugging only.

        The hot path uses :meth:`register` (which already returns the
        live snapshot in one round-trip); this method exists for
        dashboards that want to poll without affecting heartbeats.
        """
        resp = self._do_request("GET", "/active_runners")
        try:
            return ActiveRunnersResult(
                active_runners=_parse_runner_list(resp.get("active_runners", [])),
                pool_hash_summary=_parse_hash_summary(resp.get("pool_hash_summary", []) or []),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise RunnerRegistryUnavailable(
                f"malformed active_runners response: {resp!r} ({e})"
            ) from e

    # ``health_check``, ``close``, and ``_do_request`` are inherited
    # from :class:`BaseDOClient`. The non-2xx path surfaces verbatim in
    # the ``RunnerRegistryUnavailable`` message so e.g. "503
    # RUNNER_REGISTRY_DO binding missing" (v3 migration not applied) is
    # visible in logs.


def create_runner_registry_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",  # noqa: S107
    enabled_env: str = "RUNNER_REGISTRY_ENABLED",
) -> Optional[RunnerRegistryClient]:
    """Build a client from env vars, returning ``None`` when disabled.

    Three independent disable paths, all returning ``None`` so the
    spider transparently falls back to its pre-P2-E behaviour (no
    registry coordination, every runner is invisible to peers — exactly
    as today):

    - ``RUNNER_REGISTRY_ENABLED`` is unset / not in ``{"1", "true", "yes"}``
      (default OFF — single-runner deployments pay zero registry overhead);
    - either of ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN``
      is empty (the supported way to disable *all* coordinator features);
    - the URL is configured but ``/health`` does not respond (logs an
      ERROR so deployment misconfiguration surfaces early).

    Designed to mirror :func:`movie_claim_client.create_movie_claim_client_from_env`
    so wiring code can decide independently whether the per-proxy throttle,
    cross-runtime login state, movie-claim coordinator, and runner
    registry are each enabled — without juggling four sets of env vars.
    """
    raw_enabled = (os.environ.get(enabled_env) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes"}:
        logger.info(
            "Runner-registry client disabled (%s=%r) — runner is invisible to peers",
            enabled_env, os.environ.get(enabled_env, ""),
        )
        return None

    url = (os.environ.get(url_env) or "").strip()
    token = (os.environ.get(token_env) or "").strip()
    if not url or not token:
        logger.info(
            "Runner-registry client not configured (%s/%s unset) — "
            "runner is invisible to peers",
            url_env, token_env,
        )
        return None

    client = RunnerRegistryClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Runner-registry Worker URL %s is configured but /health did not respond — "
            "runner is invisible to peers for this run",
            url,
        )
        client.close()
        return None
    logger.info("Runner-registry client initialised: base_url=%s", url)
    return client
