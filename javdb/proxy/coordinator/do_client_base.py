"""Shared HTTP boilerplate for Cloudflare Worker Durable-Object clients.

All four DO clients in this package
(:class:`ProxyCoordinatorClient`,
:class:`LoginStateClient`,
:class:`MovieClaimClient`,
:class:`RunnerRegistryClient`)
talk to the same Worker over an authenticated JSON-over-HTTP wire
protocol with identical conventions:

* Bearer-token auth via the ``Authorization`` header.
* One-shot blocking calls; never retry — callers treat the per-client
  ``*Unavailable`` exception as a fail-open signal.
* ``GET /health`` is the unauthenticated liveness probe.
* Responses encode the server-side clock via either ``server_time_ms``
  (preferred) or legacy ``server_time``.

This module factors that boilerplate into :class:`BaseDOClient` so each
concrete client only owns its *wire format* (request bodies, response
dataclasses, route paths). Each subclass declares its own
``_unavailable_exc`` class so callers can keep catching the
domain-specific exception type they already know.
"""

from __future__ import annotations

from typing import Optional

import requests

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-do-client/1.0"


class DOClientUnavailable(Exception):
    """Base class for DO-client unavailability signals.

    Concrete clients subclass this so callers can either catch the
    domain-specific type (preferred) or catch the umbrella type when
    they want fail-open behaviour across every Worker dependency.
    """


class BaseDOClient:
    """Shared HTTP boilerplate for DO clients.

    Subclasses MUST set :attr:`_unavailable_exc` to their own
    ``*Unavailable`` exception class (a subclass of
    :class:`DOClientUnavailable`) so :meth:`_do_request` raises the
    type that existing call-sites already handle.
    """

    #: Override in subclasses. Used by :meth:`_do_request` to raise the
    #: correct domain-specific exception on transport / decode failures.
    _unavailable_exc: type = DOClientUnavailable

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

    @staticmethod
    def _extract_server_time_ms(data: dict) -> int:
        """Read the server-side timestamp from a Worker response.

        Prefers the explicit-units ``server_time_ms`` key and falls back
        to legacy ``server_time`` (the Worker emits ``Date.now()`` already
        in ms). Returns ``0`` when neither key is present — this matches
        :class:`RunnerRegistryClient`'s historical behaviour and lets an
        older Worker that omits the timestamp coexist with newer clients
        without raising ``KeyError`` inside the response parser.
        """
        if "server_time_ms" in data:
            return int(data["server_time_ms"])
        return int(data.get("server_time", 0) or 0)

    def health_check(self) -> bool:
        """Return ``True`` if ``GET /health`` returns 200, else ``False``.

        Default implementation: simple status-code check, swallow all
        exceptions. Subclasses (e.g. :class:`ProxyCoordinatorClient`)
        may override to emit richer diagnostics for known
        misconfiguration symptoms (CF 403, etc.).
        """
        try:
            resp = self._session.get(
                f"{self._base_url}/health", timeout=self._timeout,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 — health probe must never raise
            return False

    def _close_session(self) -> None:
        """Internal session-only cleanup. Always safe; never raises.

        Subclasses with extra cleanup (worker pools, etc.) MUST call
        this from their overridden :meth:`close` after their own
        shutdown sequence.
        """
        try:
            self._session.close()
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
            logger.warning(
                "Failed to close %s HTTP session: %s",
                type(self).__name__, exc,
            )

    def close(self) -> None:
        """Release the underlying ``requests.Session``. Idempotent.

        Subclasses with richer cleanup override this and call
        :meth:`_close_session` at the end.
        """
        self._close_session()

    def _do_request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
    ) -> dict:
        """Issue a single HTTP call and decode its JSON body.

        All failure modes (timeout, connection error, non-2xx, malformed
        or non-object JSON) collapse into :attr:`_unavailable_exc` so
        call-sites only handle one exception type. Never retries.
        """
        exc_cls = self._unavailable_exc
        url = f"{self._base_url}{path}"
        try:
            if method == "GET":
                resp = self._session.get(url, timeout=self._timeout)
            else:
                resp = self._session.post(
                    url, json=body or {}, timeout=self._timeout,
                )
        except (requests.Timeout, requests.ConnectionError) as e:
            raise exc_cls(f"network error: {e}") from e
        except requests.RequestException as e:
            raise exc_cls(f"request failed: {e}") from e

        if resp.status_code >= 300:
            raise exc_cls(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            parsed = resp.json()
        except ValueError as e:
            raise exc_cls(f"invalid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise exc_cls(
                "invalid JSON: expected object, got "
                f"{type(parsed).__name__}"
            )
        return parsed
