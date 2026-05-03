"""HTTP client for the Cloudflare Worker + Durable Object proxy coordinator.

This module is the *client* counterpart of the Worker maintained in the
sibling repo
[`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator).
It lets multiple GitHub Actions runners
(or any concurrent spider instance) coordinate per-proxy request pacing
through a single, globally-consistent authority.

Design constraints mirrored from the plan:

- One HTTP round-trip per scheduled HTTP request (``lease``); CF/failure
  reports go through a separate ``report`` endpoint and are best-effort.
- **No retries** on failure.  A coordinator outage must NOT pile up extra
  latency on top of the user-visible spider request; the caller decides
  whether to fail-open with local throttling.
- A short timeout (default 5 s) bounds the worst-case impact of a slow
  Worker; the caller's overall wait is capped by this.
- ``CoordinatorUnavailable`` carries a reason string for logging/metrics
  but does not retry internally.

The client is thread-safe: each call constructs its own request and the
underlying ``requests.Session`` only stores connection-pool metadata.
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-proxy-coordinator-client/1.0"

# P1-A — default ban duration (3 days) used by `mark_proxy_banned` and the
# Worker's `loadBanTtlMs` fallback.  Keeping the value here lets every spider
# call site stay consistent without having to thread the constant through
# config; if ops want to override globally they can set the Worker's
# `BAN_TTL_MS` via `wrangler.toml [vars]` instead.
DEFAULT_BAN_TTL_MS = 3 * 24 * 60 * 60 * 1000  # 259_200_000

# ── Async report dispatch ────────────────────────────────────────────────
# ``report_async`` is fired from CF / failure handlers in the request hot
# path (potentially many events per URL during turnstile storms). The
# previous implementation spawned a fresh ``threading.Thread`` per call,
# which could accumulate hundreds of threads each blocked on an HTTP
# timeout when the coordinator was slow — risking thread/memory exhaustion.
#
# Instead we run a small fixed pool of daemon workers behind a bounded
# queue. When the queue is full we drop new events with a throttled
# WARNING; cf/failure events are best-effort by design and the lease
# endpoint already carries the authoritative penalty factor.
_ASYNC_REPORT_WORKERS = 2
_ASYNC_REPORT_QUEUE_SIZE = 64
# Sentinel pushed at shutdown to release blocking ``queue.get`` calls.
_ASYNC_QUEUE_SENTINEL: Tuple = (None, None)


class CoordinatorUnavailable(Exception):
    """Raised when the coordinator cannot be reached or returns an error.

    This is a *signal*, not a panic: callers in the spider's hot path
    (e.g. :class:`MovieSleepManager`) catch it to fall back to local
    throttling without aborting the request.
    """


@dataclass(frozen=True)
class ProxyHealthSnapshot:
    """P2-D — derived per-proxy health metrics returned alongside a lease.

    All four fields default to ``None`` / ``0`` so older Workers that
    don't surface ``health`` look identical to the pre-P2-D behaviour;
    callers must treat ``score is None`` as "no signal, use neutral
    weighting" (the Python ``ProxyPool.next_proxy`` weighting falls back
    to a uniform distribution in that case).

    Attributes:
        success_count: Number of successful HTTP responses observed in
            the rolling window (``CF_RECENT_WINDOW_MS`` on the Worker
            side, currently 5 min).
        failure_count: Number of CF / 4xx / 5xx / connection failures
            observed in the same window.
        latency_ema_ms: Exponential moving average (alpha=0.2) of the
            ``latency_ms`` reported via ``report(kind=…, latency_ms=…)``.
            ``0`` means "no latency samples yet".
        score: Derived proxy-quality score in [0, 1]:

            - ``0.5`` when no events are recorded yet (neutral baseline).
            - Otherwise ``success_count / (success + failure)`` minus a
              latency penalty (``(latency_ema_ms - 500) / 10000``,
              capped at 0.5) so a 100%-success proxy that takes 5 s per
              request scores worse than a 100%-success 200 ms proxy.
    """

    success_count: int = 0
    failure_count: int = 0
    latency_ema_ms: float = 0.0
    score: float = 0.5


@dataclass(frozen=True)
class LeaseResult:
    """Reply from ``POST /lease``.

    Attributes:
        wait_ms: How long the caller must sleep before issuing the request.
            Already incorporates the caller's own ``intended_sleep_ms``,
            the DO's ``next_available_at``, all three throttle windows,
            and the server-side jitter.  Caller MUST honour this value
            (a literal ``time.sleep(wait_ms / 1000)``) before sending
            the HTTP request.
        penalty_factor: Cross-instance shared penalty factor derived from
            recent CF/failure events on this proxy.  Used to inform local
            sleep adaptation; see :meth:`PenaltyTracker.set_remote_factor`.
        server_time_ms: Server-side ``Date.now()`` at lease grant time, for
            clock-skew diagnostics.
        reason: Why the wait is what it is — useful for logs.  When the
            proxy is currently banned the Worker emits ``"banned"`` (the
            P1-A addition); old Worker deploys never produce this value.
        banned: P1-A — ``True`` iff the Worker reports the proxy as
            currently banned at lease time.  Defaults to ``False`` so
            old Workers (which never set this key) keep the
            pre-coordinator behaviour.
        banned_until: P1-A — wall-clock ms epoch when the current ban
            auto-expires; ``None`` when not banned (or when the Worker
            predates this field).
        requires_cf_bypass: P1-A — ``True`` iff the proxy needs CF bypass
            to talk to JavDB.  Mirrors ``state.proxies_requiring_cf_bypass``
            on the Python side; defaults to ``False`` for old Workers.
        cf_bypass_until: P1-A — wall-clock ms epoch when CF bypass
            requirement auto-expires.  ``0`` is the *permanent for this
            session* sentinel (mirrors ``state.always_bypass_time == 0``);
            ``None`` when no bypass requirement is currently set or the
            Worker predates this field.
    """

    wait_ms: int
    penalty_factor: float
    server_time_ms: int
    reason: str
    # P1-A — all four optional fields default to "no signal", so a Worker
    # that doesn't know about them looks identical to the old behaviour.
    banned: bool = False
    banned_until: Optional[int] = None
    requires_cf_bypass: bool = False
    cf_bypass_until: Optional[int] = None
    # P2-D — derived per-proxy health snapshot. ``None`` means the Worker
    # predates the field; callers must fall back to uniform weighting.
    health: Optional[ProxyHealthSnapshot] = None


@dataclass(frozen=True)
class ReportResult:
    """Reply from ``POST /report``."""

    penalty_factor: float
    recent_event_count: int
    server_time_ms: int


def _normalize_proxy_id(raw: Optional[str], *, fallback_seed: Optional[str] = None) -> str:
    """Deterministically normalise a proxy identifier for DO addressing.

    All runners must derive the same string for the same physical proxy,
    or the per-proxy DO mutex falls apart silently.  The rule is:

    1. If *raw* is a non-empty string, strip whitespace and use it verbatim.
    2. Otherwise, if *fallback_seed* is provided (typically ``host:port``),
       hash it to a stable 16-char hex digest and prefix ``proxy-``.
    3. Otherwise, raise :class:`ValueError` so the bug surfaces loudly.

    Returns a string of length 1..256 (the DO ``idFromName`` limit).
    """
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed:
            return trimmed[:256]
    if fallback_seed:
        # Not a security-critical hash — only used to bucket a configurable
        # ``host:port`` into a stable 16-char DO key. ``usedforsecurity=False``
        # silences the Bandit S324 / Ruff S324 lint without changing the
        # produced digest, so existing runners that may have already derived
        # IDs continue to agree on the same DO key.
        digest = hashlib.sha1(  # noqa: S324 — see comment above
            fallback_seed.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16]
        derived = f"proxy-{digest}"
        logger.warning(
            "Coordinator proxy_id derived from host:port hash: %s — "
            "recommend setting `name` in PROXY_POOL_JSON so all runners agree",
            derived,
        )
        return derived
    raise ValueError("proxy_id is empty and no fallback_seed was provided")


# P1-A — ``ban`` / ``unban`` / ``cf_bypass`` are *out-of-band* report kinds
# that mutate the Worker's per-proxy ``bannedUntil`` / ``cfBypassUntil`` state
# fields without touching the throttle history (``cfEvents`` / penalty factor).
# See ``JAVDB_AutoSpider_Proxycoordinator/src/proxy_coordinator.ts`` handleReport.
_VALID_REPORT_KINDS = (
    "cf",
    "failure",
    "ban",
    "unban",
    "cf_bypass",
    # P2-D — successful HTTP completion. Augments ``success_count`` and
    # the latency EMA on the Worker side, but does NOT touch the cf-event
    # bucket / penalty factor (kept distinct from "cf" on purpose).
    "success",
)


def _validate_kind(kind: str) -> str:
    """Validate ``kind`` against the wire-protocol's accepted values.

    Surfaces typos (e.g. ``"cF"``, ``"fail"``) loudly at call time instead of
    silently coercing them to ``"cf"`` — the previous behaviour caused
    misclassified events to be reported under the CF bucket without warning.
    """
    if kind not in _VALID_REPORT_KINDS:
        raise ValueError(
            f"Invalid kind: {kind!r}; expected one of {_VALID_REPORT_KINDS}"
        )
    return kind


def _extract_server_time_ms(data: dict) -> int:
    """Read the server-side timestamp from a coordinator response.

    Prefers the ``server_time_ms`` wire key (matches the dataclass field
    name) and falls back to ``server_time`` for backward compatibility with
    the current Worker, which emits ``server_time`` from a ``Date.now()``
    call (already in milliseconds). The fallback lets the Worker migrate to
    the explicit ``server_time_ms`` key without coordinated Python deploys.
    """
    if "server_time_ms" in data:
        return int(data["server_time_ms"])
    return int(data["server_time"])


class ProxyCoordinatorClient:
    """HTTP client for the proxy-coordinator Worker.

    Construct once per process and pass into :class:`MovieSleepManager` and
    :class:`PenaltyTracker`.  Both methods are blocking and short-lived;
    callers that need non-blocking ``report`` should use
    :meth:`report_async`.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``.
        token: Bearer token (must match the secret set via
            ``wrangler secret put PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SEC,
        user_agent: str = _DEFAULT_USER_AGENT,
        async_workers: int = _ASYNC_REPORT_WORKERS,
        async_queue_size: int = _ASYNC_REPORT_QUEUE_SIZE,
    ):
        if not base_url or not isinstance(base_url, str):
            raise ValueError("base_url must be a non-empty string")
        if not token or not isinstance(token, str):
            raise ValueError("token must be a non-empty string")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = float(timeout)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        })
        self._async_worker_count = max(1, int(async_workers))
        # Bounded fire-and-forget pool for ``report_async``. Keep capacity at
        # least equal to the worker count so shutdown can enqueue one sentinel
        # per worker after draining pending events.
        self._async_queue: "queue.Queue[Tuple[Optional[str], Optional[str]]]" = (
            queue.Queue(maxsize=max(self._async_worker_count, int(async_queue_size)))
        )
        self._async_dropped = 0
        self._async_lock = threading.Lock()
        self._async_shutdown = False
        self._async_workers: List[threading.Thread] = []
        # P2-D — cache the most recent ``health`` snapshot per proxy so
        # ``ProxyPool.next_proxy`` can read scores without a per-pick HTTP
        # round-trip.  Populated as a side-effect of ``lease()``; survives
        # for the lifetime of the client.  Keyed by the *normalised* proxy
        # id so lookups from the pool (which uses the configured ``name``)
        # match the keys produced by leases.
        self._health_cache: Dict[str, ProxyHealthSnapshot] = {}
        self._health_cache_lock = threading.Lock()

    def _close_session(self) -> None:
        try:
            self._session.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must be best-effort
            logger.warning("Failed to close proxy coordinator HTTP session: %s", exc)

    @property
    def base_url(self) -> str:
        return self._base_url

    # -- public API ---------------------------------------------------------

    def lease(self, proxy_id: str, intended_sleep_ms: int) -> LeaseResult:
        """Request permission to issue a request on *proxy_id*.

        Returns immediately with a :class:`LeaseResult` whose ``wait_ms``
        the caller MUST honour.  Raises :class:`CoordinatorUnavailable`
        on any failure (timeout, non-2xx, connection error, malformed
        response).  Never retries.
        """
        # _normalize_proxy_id raises ValueError on bad/missing input; route it
        # through CoordinatorUnavailable so the spider's hot path stays on the
        # documented fail-open contract instead of crashing the worker.
        try:
            normalized = _normalize_proxy_id(proxy_id)
        except ValueError as e:
            raise CoordinatorUnavailable(f"invalid proxy_id: {e}") from e
        intended = max(0, int(intended_sleep_ms))
        try:
            resp = self._session.post(
                f"{self._base_url}/lease",
                json={"proxy_id": normalized, "intended_sleep_ms": intended},
                timeout=self._timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            raise CoordinatorUnavailable(f"network error: {e}") from e
        except requests.RequestException as e:
            raise CoordinatorUnavailable(f"request failed: {e}") from e

        if resp.status_code >= 300:
            raise CoordinatorUnavailable(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise CoordinatorUnavailable(f"invalid JSON: {e}") from e

        try:
            # P1-A — surface ban / cf_bypass piggy-backed on the lease.  All
            # four fields are optional on the wire so an older Worker keeps
            # working unchanged.  ``banned_until`` / ``cf_bypass_until`` are
            # left as ``None`` when the key is missing OR when the Worker
            # explicitly sends ``null``; ``cf_bypass_until == 0`` is preserved
            # because it carries the *permanent for this session* meaning.
            banned_until_raw = data.get("banned_until")
            banned_until = (
                int(banned_until_raw) if banned_until_raw is not None else None
            )
            cf_bypass_until_raw = data.get("cf_bypass_until")
            cf_bypass_until = (
                int(cf_bypass_until_raw) if cf_bypass_until_raw is not None else None
            )
            # P2-D — ``health`` is optional. We treat a missing key, ``null``,
            # or a non-dict value as "no signal" (-> ``None``) so a malformed
            # response from a partially-deployed Worker can never crash the
            # spider. ``ProxyPool.next_proxy`` falls back to uniform weighting
            # when ``health is None``.
            health_raw = data.get("health")
            health: Optional[ProxyHealthSnapshot]
            if isinstance(health_raw, dict):
                try:
                    health = ProxyHealthSnapshot(
                        success_count=int(health_raw.get("success_count", 0)),
                        failure_count=int(health_raw.get("failure_count", 0)),
                        latency_ema_ms=float(health_raw.get("latency_ema_ms", 0.0)),
                        score=float(health_raw.get("score", 0.5)),
                    )
                except (TypeError, ValueError):
                    health = None
            else:
                health = None
            # P2-D — refresh the cache so subsequent ``get_proxy_health()``
            # calls (driven by ``ProxyPool.next_proxy`` weighting) see the
            # latest snapshot without an extra round-trip.
            if health is not None:
                with self._health_cache_lock:
                    self._health_cache[normalized] = health
            return LeaseResult(
                wait_ms=int(data["wait_ms"]),
                penalty_factor=float(data["penalty_factor"]),
                server_time_ms=_extract_server_time_ms(data),
                reason=str(data.get("reason", "ok")),
                banned=bool(data.get("banned", False)),
                banned_until=banned_until,
                requires_cf_bypass=bool(data.get("requires_cf_bypass", False)),
                cf_bypass_until=cf_bypass_until,
                health=health,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise CoordinatorUnavailable(
                f"malformed response: {data!r} ({e})"
            ) from e

    def report(
        self,
        proxy_id: str,
        kind: str = "cf",
        *,
        ttl_ms: Optional[int] = None,
        reason: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> ReportResult:
        """Report a CF / failure / ban / unban / cf_bypass / success event on *proxy_id*.

        Same failure semantics as :meth:`lease` — never retries, raises
        :class:`CoordinatorUnavailable` on any error. ``kind`` must be one of
        :data:`_VALID_REPORT_KINDS`; passing anything else raises
        :class:`ValueError` so typos surface at call time rather than being
        silently bucketed under ``"cf"``.

        ``ttl_ms`` is honoured by ``kind="ban"`` and ``kind="cf_bypass"``;
        ignored otherwise.  ``reason`` is a free-form ops annotation
        (e.g. ``"manual"``, ``"penalty_2"``); the Worker stores it in the
        analytics dataset but does not act on it.

        ``latency_ms`` (P2-D) is folded into the per-proxy latency EMA on
        the Worker side and is honoured for any ``kind`` (typically
        ``"success"`` or ``"failure"`` from the request handler).  ``None``
        skips the EMA update.

        See ``JAVDB_AutoSpider_Proxycoordinator/src/proxy_coordinator.ts``
        ``handleReport`` for the precise tri-state semantics of
        ``cf_bypass`` (``ttl_ms == 0`` / omitted = permanent for this
        session; ``> 0`` = wall-clock TTL window).
        """
        _validate_kind(kind)
        try:
            normalized = _normalize_proxy_id(proxy_id)
        except ValueError as e:
            raise CoordinatorUnavailable(f"invalid proxy_id: {e}") from e
        body: dict = {"proxy_id": normalized, "kind": kind}
        if ttl_ms is not None:
            body["ttl_ms"] = int(ttl_ms)
        if reason is not None:
            body["reason"] = str(reason)
        if latency_ms is not None:
            # Clamp negatives to 0 so a clock-skew artefact can't poison the
            # EMA.  The Worker also defends against this but doing it here
            # keeps the wire payload tidy.
            body["latency_ms"] = max(0, int(latency_ms))
        try:
            resp = self._session.post(
                f"{self._base_url}/report",
                json=body,
                timeout=self._timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            raise CoordinatorUnavailable(f"network error: {e}") from e
        except requests.RequestException as e:
            raise CoordinatorUnavailable(f"request failed: {e}") from e

        if resp.status_code >= 300:
            raise CoordinatorUnavailable(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise CoordinatorUnavailable(f"invalid JSON: {e}") from e

        try:
            return ReportResult(
                penalty_factor=float(data["penalty_factor"]),
                recent_event_count=int(data["recent_event_count"]),
                server_time_ms=_extract_server_time_ms(data),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise CoordinatorUnavailable(
                f"malformed response: {data!r} ({e})"
            ) from e

    def report_async(
        self,
        proxy_id: str,
        kind: str = "cf",
        *,
        ttl_ms: Optional[int] = None,
        reason: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        """Fire-and-forget variant of :meth:`report`.

        Intended for use inside ``PenaltyTracker.record_event()`` where the
        caller (a CF/failure detection handler) MUST NOT block. Events are
        funneled into a bounded queue served by a small daemon-thread pool
        so a slow/unavailable coordinator can't accumulate unbounded
        threads or memory during turnstile storms. When the queue is full
        the event is dropped and a throttled WARNING is logged; the next
        successful ``lease`` call will still fetch the authoritative
        penalty factor from the server.

        ``kind`` is validated synchronously so typos surface at the call
        site (a ``ValueError``) rather than being swallowed by the worker's
        broad exception handler 5–500 ms later.

        ``ttl_ms`` / ``reason`` / ``latency_ms`` are forwarded verbatim to
        :meth:`report` (used by the P1-A ``ban`` / ``cf_bypass`` kinds and
        the P2-D ``success`` / ``failure`` kinds, respectively).
        """
        _validate_kind(kind)
        with self._async_lock:
            if self._async_shutdown:
                return
            if not self._async_workers:
                for i in range(self._async_worker_count):
                    t = threading.Thread(
                        target=self._async_report_loop,
                        name=f"coord-report-{i}",
                        daemon=True,
                    )
                    t.start()
                    self._async_workers.append(t)
            try:
                # Always enqueue a 5-tuple; the worker unpacks defensively so
                # legacy 2-/4-tuples in flight at upgrade time still work.
                self._async_queue.put_nowait(
                    (proxy_id, kind, ttl_ms, reason, latency_ms)
                )
            except queue.Full:
                self._async_dropped += 1
                count = self._async_dropped
            else:
                return
            # Log first drop loudly, then once every 50 to avoid log floods
            # while still surfacing sustained backpressure.
            if count == 1 or count % 50 == 0:
                logger.warning(
                    "Coordinator report_async queue full "
                    "(capacity=%d, workers=%d); dropping event "
                    "proxy_id=%s kind=%s (total dropped=%d)",
                    self._async_queue.maxsize, len(self._async_workers),
                    proxy_id, kind, count,
                )

    # ── P1-A convenience helpers ─────────────────────────────────────────
    # These wrap ``report_async`` so call-sites in ``proxy_ban_manager`` and
    # ``runtime/state.mark_proxy_cf_bypass`` don't have to remember the
    # ``ttl_ms`` defaults / kind strings.  All are fire-and-forget and inherit
    # the queue-full / unavailable behaviour of ``report_async``.

    def mark_proxy_banned(
        self,
        proxy_id: str,
        *,
        ttl_ms: int = DEFAULT_BAN_TTL_MS,
        reason: Optional[str] = None,
    ) -> None:
        """Persist a cross-runner ban on *proxy_id* (default 3 days).

        The Worker takes the maximum of any concurrent ban TTL so two
        runners reporting different durations on the same proxy never
        accidentally shorten an existing ban.  Auto-expires server-side;
        no client-side cleanup is required.
        """
        self.report_async(proxy_id, "ban", ttl_ms=ttl_ms, reason=reason)

    def mark_proxy_unbanned(
        self,
        proxy_id: str,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """Clear any active ban on *proxy_id* immediately."""
        self.report_async(proxy_id, "unban", reason=reason)

    def mark_cf_bypass(
        self,
        proxy_id: str,
        *,
        ttl_ms: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Flag *proxy_id* as needing CF bypass.

        Mirrors ``state.mark_proxy_cf_bypass`` semantics:

        - ``ttl_ms is None`` or ``ttl_ms == 0`` → permanent for this session
          (the Worker stores ``cfBypassUntil = 0``, which is sticky and never
          downgraded by a follow-up finite-TTL refresh).
        - ``ttl_ms > 0`` → expires ``ttl_ms`` after the Worker receives it.
        """
        self.report_async(proxy_id, "cf_bypass", ttl_ms=ttl_ms, reason=reason)

    def _async_report_loop(self) -> None:
        """Worker loop: drain the async queue serialising calls to ``report``."""
        while True:
            item = self._async_queue.get()
            try:
                if item is _ASYNC_QUEUE_SENTINEL or item[0] is None:
                    return
                # Backwards-compat: existing tests / call sites may push a
                # 2-tuple ``(proxy_id, kind)`` or a 4-tuple including
                # ``ttl_ms`` / ``reason``; new P2-D sites push a 5-tuple
                # adding ``latency_ms``.  Unpack defensively.
                proxy_id = item[0]
                kind = item[1]
                ttl_ms = item[2] if len(item) > 2 else None
                reason = item[3] if len(item) > 3 else None
                latency_ms = item[4] if len(item) > 4 else None
                # Only forward optional kwargs when explicitly set so test
                # stubs that monkeypatch ``report`` with the legacy 2-arg
                # signature stay compatible.
                kwargs: dict = {}
                if ttl_ms is not None:
                    kwargs["ttl_ms"] = ttl_ms
                if reason is not None:
                    kwargs["reason"] = reason
                if latency_ms is not None:
                    kwargs["latency_ms"] = latency_ms
                try:
                    self.report(proxy_id, kind, **kwargs)
                except CoordinatorUnavailable as e:
                    logger.warning("Coordinator report_async failed: %s", e)
                except Exception:  # noqa: BLE001 — must never escape a daemon worker
                    logger.exception("Coordinator report_async crashed unexpectedly")
            finally:
                self._async_queue.task_done()

    def _enqueue_async_sentinel(self) -> None:
        """Reliably enqueue one shutdown sentinel for a worker without blocking."""
        while True:
            try:
                self._async_queue.put_nowait(_ASYNC_QUEUE_SENTINEL)
                return
            except queue.Full:
                try:
                    self._async_queue.get_nowait()
                except queue.Empty:
                    continue
                self._async_queue.task_done()

    def close(self, *, wait: bool = False, timeout: Optional[float] = None) -> None:
        """Stop the async-report worker pool. Idempotent.

        Daemon workers exit at process shutdown anyway, but tests and
        long-lived hosts that recycle clients should call this to release
        blocked HTTP sockets and join the threads cleanly.

        Args:
            wait: If ``True``, block until each worker exits.
            timeout: Per-worker ``Thread.join`` timeout in seconds.
        """
        with self._async_lock:
            if self._async_shutdown:
                workers = list(self._async_workers)
                already_shutdown = True
            else:
                self._async_shutdown = True
                workers = list(self._async_workers)
                already_shutdown = False
        if already_shutdown:
            if wait:
                for t in workers:
                    t.join(timeout=timeout)
            self._close_session()
            return
        # Drop any pending events so we can guarantee enqueueing exactly
        # one sentinel per worker even when the queue was previously full.
        while True:
            try:
                self._async_queue.get_nowait()
            except queue.Empty:
                break
            self._async_queue.task_done()
        for _ in workers:
            self._enqueue_async_sentinel()
        if wait:
            for t in workers:
                t.join(timeout=timeout)
        self._close_session()

    def get_proxy_health(self, proxy_id: str) -> Optional[ProxyHealthSnapshot]:
        """P2-D — return the most recently observed health snapshot, if any.

        Reads the in-process cache populated as a side-effect of
        :meth:`lease`; never fires its own HTTP request.  Returns ``None``
        when the proxy hasn't been leased yet (so ``ProxyPool.next_proxy``
        falls back to the neutral 0.5 baseline) or when the proxy id is
        empty/invalid.  Thread-safe.
        """
        if not proxy_id or not isinstance(proxy_id, str):
            return None
        try:
            normalized = _normalize_proxy_id(proxy_id)
        except ValueError:
            return None
        with self._health_cache_lock:
            return self._health_cache.get(normalized)

    def get_proxy_health_score(self, proxy_id: str) -> Optional[float]:
        """Convenience wrapper that returns just the ``score`` field.

        ``ProxyPool`` accepts ``Callable[[str], Optional[float]]`` as its
        ``health_provider`` so this method matches that signature
        directly: callers can pass ``client.get_proxy_health_score`` as
        the provider.
        """
        snap = self.get_proxy_health(proxy_id)
        return snap.score if snap is not None else None

    def health_check(self) -> bool:
        """Return ``True`` if ``GET /health`` returns 200, else ``False``.

        Used at startup to log a clear ERROR when the coordinator URL is
        misconfigured, before any request takes the cost of a fail-open.
        Never raises.
        """
        try:
            resp = self._session.get(f"{self._base_url}/health", timeout=self._timeout)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False


def create_coordinator_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",
) -> Optional[ProxyCoordinatorClient]:
    """Build a coordinator client from environment variables.

    Returns ``None`` (silently) when either env var is unset or empty,
    which is the supported way to disable the coordinator and fall back
    to purely local throttling.

    Returns ``None`` (and logs an ERROR) when the URL is configured but
    ``/health`` is unreachable; this surfaces deployment misconfiguration
    early without breaking the spider.
    """
    url = (os.environ.get(url_env) or "").strip()
    token = (os.environ.get(token_env) or "").strip()
    if not url or not token:
        logger.info(
            "Proxy coordinator not configured (%s/%s unset) — using local throttling only",
            url_env, token_env,
        )
        return None

    client = ProxyCoordinatorClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Proxy coordinator URL %s is configured but /health did not respond — "
            "falling back to local throttling for this run",
            url,
        )
        client.close()
        return None
    logger.info(
        "Proxy coordinator client initialised: base_url=%s",
        url,
    )
    return client
