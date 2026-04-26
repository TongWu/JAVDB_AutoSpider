"""HTTP client for the Cloudflare Worker + Durable Object proxy coordinator.

This module is the *client* counterpart of the Worker living in
``cloudflare/proxy_coordinator/``.  It lets multiple GitHub Actions runners
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
import threading
from dataclasses import dataclass
from typing import Optional

import requests

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-proxy-coordinator-client/1.0"


class CoordinatorUnavailable(Exception):
    """Raised when the coordinator cannot be reached or returns an error.

    This is a *signal*, not a panic: callers in the spider's hot path
    (e.g. :class:`MovieSleepManager`) catch it to fall back to local
    throttling without aborting the request.
    """


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
        reason: Why the wait is what it is — useful for logs.
    """

    wait_ms: int
    penalty_factor: float
    server_time_ms: int
    reason: str


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
        digest = hashlib.sha1(fallback_seed.encode("utf-8")).hexdigest()[:16]
        derived = f"proxy-{digest}"
        logger.warning(
            "Coordinator proxy_id derived from host:port hash: %s — "
            "recommend setting `name` in PROXY_POOL_JSON so all runners agree",
            derived,
        )
        return derived
    raise ValueError("proxy_id is empty and no fallback_seed was provided")


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
        normalized = _normalize_proxy_id(proxy_id)
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
            return LeaseResult(
                wait_ms=int(data["wait_ms"]),
                penalty_factor=float(data["penalty_factor"]),
                server_time_ms=int(data["server_time"]),
                reason=str(data.get("reason", "ok")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise CoordinatorUnavailable(
                f"malformed response: {data!r} ({e})"
            ) from e

    def report(self, proxy_id: str, kind: str = "cf") -> ReportResult:
        """Report a CF / failure event on *proxy_id*.

        Same failure semantics as :meth:`lease` — never retries, raises
        :class:`CoordinatorUnavailable` on any error.
        """
        if kind not in ("cf", "failure"):
            kind = "cf"
        normalized = _normalize_proxy_id(proxy_id)
        try:
            resp = self._session.post(
                f"{self._base_url}/report",
                json={"proxy_id": normalized, "kind": kind},
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
                server_time_ms=int(data["server_time"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise CoordinatorUnavailable(
                f"malformed response: {data!r} ({e})"
            ) from e

    def report_async(self, proxy_id: str, kind: str = "cf") -> None:
        """Fire-and-forget variant of :meth:`report`.

        Intended for use inside ``PenaltyTracker.record_event()`` where the
        caller (a CF/failure detection handler) MUST NOT block.  Errors
        are swallowed and only logged at WARNING level.
        """

        def _send():
            try:
                self.report(proxy_id, kind)
            except CoordinatorUnavailable as e:
                logger.warning("Coordinator report_async failed: %s", e)
            except Exception:  # noqa: BLE001 — must never escape a daemon thread
                logger.exception("Coordinator report_async crashed unexpectedly")

        t = threading.Thread(target=_send, daemon=True, name="coord-report")
        t.start()

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
        return None
    logger.info(
        "Proxy coordinator client initialised: base_url=%s",
        url,
    )
    return client
