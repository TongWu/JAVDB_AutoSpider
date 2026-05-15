"""HTTP client for the Cloudflare Worker + Durable Object login-state coordinator.

This is the *client* counterpart of the ``GlobalLoginState`` Durable Object
maintained in the sibling repo
[`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator).
Sister to :mod:`packages.python.javdb_platform.proxy_coordinator_client`
(which targets the per-proxy ``ProxyCoordinator`` DO); both reuse the same
``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN`` env vars and the
same Worker — only the URL paths differ.

Design constraints (mirror those of :mod:`proxy_coordinator_client`):

- One HTTP round-trip per call; **no retries** on failure.  Callers MUST
  treat :class:`LoginStateUnavailable` as a fail-open signal and fall
  back to the existing per-runner login behaviour, never as a fatal
  error.
- A short timeout (default 5 s) bounds the worst-case impact of a slow
  Worker; the spider's hot path is never blocked for more than that.
- The client is thread-safe: each call constructs its own request and the
  underlying ``requests.Session`` only stores connection-pool metadata.

Wire format and field semantics are documented in
[`src/types.ts`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator/blob/main/src/types.ts)
of the Worker repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

from packages.python.javdb_platform.do_client_base import (
    BaseDOClient,
    DOClientUnavailable,
)
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0


# B.11 (2026-05-11): keys that may carry the live session cookie or
# anything else worth redacting before we bake the raw dict into an
# exception message. Anything not in this allow-list survives so
# operators still see useful schema info like the missing key names.
_LOGIN_RESP_REDACTED_KEYS = frozenset({"cookie", "cookie_ciphertext"})


def _redact_resp(resp) -> dict:
    """Return a shallow copy of *resp* with sensitive fields stubbed.

    ``LoginStateClient`` raises ``LoginStateUnavailable`` with the raw
    response embedded when JSON parsing of one of its methods fails. If
    the only thing wrong is a missing key, the rest of the dict still
    contains the decrypted ``cookie`` — and exception text routinely
    survives in CI logs / Sentry / operator transcripts long after the
    cookie remains valid. Redact the live cookie field down to a length
    indicator so debugging info (other fields, the exception's parser
    error) is preserved without leaking the secret.
    """
    if not isinstance(resp, dict):
        return {"_resp_type": type(resp).__name__}
    out = {}
    for k, v in resp.items():
        if k in _LOGIN_RESP_REDACTED_KEYS and isinstance(v, str) and v:
            out[k] = f"<redacted length={len(v)}>"
        else:
            out[k] = v
    return out
_DEFAULT_USER_AGENT = "javdb-spider-login-state-client/1.0"

# Server-side bounds on ``ttl_ms`` (mirror ``LOGIN_LEASE_TTL_*_MS`` in the
# Worker).  Exposed here so callers can stay within range without an extra
# round-trip; the Worker still clamps defensively if a caller goes over.
LEASE_TTL_MIN_MS = 5_000
LEASE_TTL_MAX_MS = 300_000


class LoginStateUnavailable(DOClientUnavailable):
    """Raised when the login-state Worker cannot be reached or returns an error.

    This is a *signal*, not a panic.  Every callsite in the spider treats
    it as "fall back to per-runner login" — the local
    :class:`LoginCoordinator` then behaves exactly as it did before the
    cross-runtime DO existed.
    """


@dataclass(frozen=True)
class LoginStateGetResult:
    """Reply from ``GET /login_state``.

    Attributes:
        proxy_name: Currently logged-in proxy, or ``None`` if none.
        cookie: Decrypted session cookie, or ``None`` if no valid cookie
            is published *or* if the Worker could not decrypt the stored
            ciphertext (e.g. after a ``PROXY_COORDINATOR_TOKEN`` rotation).
            In both cases the next runner should treat the session as
            stale and re-login.
        version: Monotonic version counter.  Incremented by every
            successful ``publish`` and ``invalidate``.  ``0`` before the
            first publish.
        last_verified_at: ``Date.now()`` (ms epoch) of the last publish;
            ``0`` if never.
        has_active_lease: ``True`` iff some runner currently holds the
            re-login mutex.  Holder identity is intentionally not exposed.
        server_time_ms: Server clock at response time, for skew diagnostics.
    """

    proxy_name: Optional[str]
    cookie: Optional[str]
    version: int
    last_verified_at: int
    has_active_lease: bool
    server_time_ms: int


@dataclass(frozen=True)
class AcquireLeaseResult:
    """Reply from ``POST /login_state/acquire_lease``.

    ``acquired`` is ``True`` when the caller now owns the lease (either a
    fresh acquire or an idempotent renewal by the same holder).  When
    ``False``, ``holder_id`` and ``lease_expires_at`` describe the *current*
    owner so callers can decide whether to back off briefly or park their
    work indefinitely.

    P2-C — ``cooldown_until_ms`` is set to a non-zero ms epoch when the
    cross-runner failure rate has crossed the Worker's
    ``LOGIN_COOLDOWN_THRESHOLD`` inside ``LOGIN_COOLDOWN_WINDOW_SEC``.
    The lease is **still granted** when set; the caller is responsible
    for parking its login flow until ``cooldown_until_ms`` so concurrent
    runners don't burn through more attempts during the back-off.
    Defaults to ``0`` for backward-compat with pre-P2-C Workers
    (which simply omit the field).  ``recent_attempt_count`` surfaces
    how many entries are currently in the rolling window — ops only,
    the spider does not branch on it.
    """

    acquired: bool
    holder_id: str
    target_proxy_name: str
    lease_expires_at: int
    server_time_ms: int
    cooldown_until_ms: int = 0
    recent_attempt_count: int = 0


@dataclass(frozen=True)
class PublishResult:
    """Reply from ``POST /login_state/publish``.

    ``version`` is the *new* version (i.e. ``previous + 1``); cache it on
    the caller side and pass it back to ``invalidate`` later as the
    optimistic-lock token.
    """

    ok: bool
    version: int
    server_time_ms: int


@dataclass(frozen=True)
class InvalidateResult:
    """Reply from ``POST /login_state/invalidate``.

    ``invalidated`` is ``False`` when the caller's ``version`` did not
    match the server's view — typically because another runner already
    published a fresher cookie.  ``current_version`` is always populated
    so the caller can resync without a separate ``get_state`` call.
    """

    invalidated: bool
    current_version: int
    server_time_ms: int


@dataclass(frozen=True)
class RecordAttemptResult:
    """Reply from ``POST /login_state/record_attempt`` (P2-C).

    ``recent_attempt_count`` covers all outcomes (success + failure) in
    the rolling window; ``recent_failure_count`` is the subset used by
    the cooldown function.  ``cooldown_until_ms`` is recomputed against
    the current buffer after the new record is appended, so the caller
    can ack the next ``acquire_lease`` decision without an extra
    round-trip.
    """

    recent_attempt_count: int
    recent_failure_count: int
    cooldown_until_ms: int
    server_time_ms: int


@dataclass(frozen=True)
class ReleaseLeaseResult:
    """Reply from ``POST /login_state/release_lease``.

    ``released`` is ``False`` when the caller is not the current holder
    (e.g. its lease already expired and was reclaimed by another runner).
    The caller can safely treat this as a no-op.
    """

    released: bool
    server_time_ms: int


def _extract_server_time_ms(data: dict) -> int:
    """Read the server-side timestamp from a coordinator response.

    Module-level alias of :meth:`BaseDOClient._extract_server_time_ms`
    so existing tests / call-sites can keep importing it from this
    module unchanged.
    """
    return BaseDOClient._extract_server_time_ms(data)


class LoginStateClient(BaseDOClient):
    """HTTP client for the GlobalLoginState DO.

    Construct once per process and pass into the runtime's
    :class:`LoginCoordinator`.  All five methods are blocking and
    short-lived; the spider's hot path is bounded by ``timeout`` (5 s
    default) on every call.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``
            (same Worker that hosts the per-proxy throttle endpoints).
        token: Bearer token (must match the secret set via
            ``wrangler secret put PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    _unavailable_exc = LoginStateUnavailable

    # -- public API ---------------------------------------------------------

    def get_state(self) -> LoginStateGetResult:
        """Read the current published login state.  Never retries.

        Returns immediately with a :class:`LoginStateGetResult`; raises
        :class:`LoginStateUnavailable` on any failure (timeout, non-2xx,
        connection error, malformed response).
        """
        resp = self._do_request("GET", "/login_state")
        try:
            return LoginStateGetResult(
                proxy_name=resp.get("proxy_name"),
                cookie=resp.get("cookie"),
                version=int(resp["version"]),
                last_verified_at=int(resp["last_verified_at"]),
                has_active_lease=bool(resp["has_active_lease"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed get_state response: {_redact_resp(resp)!r} ({e})"
            ) from e

    def acquire_lease(
        self,
        holder_id: str,
        target_proxy_name: str,
        ttl_ms: int,
    ) -> AcquireLeaseResult:
        """Try to acquire (or renew) the re-login mutex.

        ``ttl_ms`` is clamped to ``[LEASE_TTL_MIN_MS, LEASE_TTL_MAX_MS]``
        on the server; passing values outside this range is harmless.

        Returns ``AcquireLeaseResult.acquired = False`` (not an exception)
        when another holder owns the lease — that is the normal "park and
        retry later" path.  Only network/server errors raise
        :class:`LoginStateUnavailable`.
        """
        if not holder_id:
            raise LoginStateUnavailable("holder_id must be a non-empty string")
        if not target_proxy_name:
            raise LoginStateUnavailable(
                "target_proxy_name must be a non-empty string",
            )
        body = {
            "holder_id": holder_id,
            "target_proxy_name": target_proxy_name,
            "ttl_ms": int(ttl_ms),
        }
        resp = self._do_request("POST", "/login_state/acquire_lease", body)
        try:
            return AcquireLeaseResult(
                acquired=bool(resp["acquired"]),
                holder_id=str(resp["holder_id"]),
                target_proxy_name=str(resp["target_proxy_name"]),
                lease_expires_at=int(resp["lease_expires_at"]),
                server_time_ms=_extract_server_time_ms(resp),
                cooldown_until_ms=int(resp.get("cooldown_until_ms", 0) or 0),
                recent_attempt_count=int(resp.get("recent_attempt_count", 0) or 0),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed acquire_lease response: {_redact_resp(resp)!r} ({e})"
            ) from e

    def publish(
        self,
        holder_id: str,
        proxy_name: str,
        cookie: str,
    ) -> PublishResult:
        """Publish a freshly-obtained session cookie.

        The caller MUST hold a live lease on the same ``holder_id`` —
        otherwise the Worker returns ``409 lease_required`` which surfaces
        as :class:`LoginStateUnavailable`.  After a successful publish the
        lease is intentionally **not** released; call
        :meth:`release_lease` once your own verification (e.g. fixed-page
        re-fetch) confirms the cookie works.
        """
        if not holder_id or not proxy_name or not cookie:
            raise LoginStateUnavailable(
                "holder_id, proxy_name, and cookie must all be non-empty",
            )
        body = {"holder_id": holder_id, "proxy_name": proxy_name, "cookie": cookie}
        resp = self._do_request("POST", "/login_state/publish", body)
        try:
            return PublishResult(
                ok=bool(resp["ok"]),
                version=int(resp["version"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed publish response: {_redact_resp(resp)!r} ({e})"
            ) from e

    def invalidate(self, version: int) -> InvalidateResult:
        """Mark the current published cookie bad with optimistic version lock.

        Pass the ``version`` the caller observed via :meth:`get_state` /
        :meth:`publish`.  The Worker only clears the cookie when its
        cached version matches — this prevents a runner working off a
        stale view from wiping a freshly-published cookie.

        Returns ``InvalidateResult.invalidated = False`` when the version
        does not match (caller should resync from
        ``current_version``).
        """
        body = {"version": int(version)}
        resp = self._do_request("POST", "/login_state/invalidate", body)
        try:
            return InvalidateResult(
                invalidated=bool(resp["invalidated"]),
                current_version=int(resp["current_version"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed invalidate response: {_redact_resp(resp)!r} ({e})"
            ) from e

    def record_attempt(
        self,
        holder_id: str,
        proxy_name: str,
        outcome: str,
    ) -> RecordAttemptResult:
        """Record a login attempt outcome on the cross-runner DO (P2-C).

        ``outcome`` MUST be one of ``"success"`` or ``"failure"``;
        anything else raises :class:`LoginStateUnavailable` (since the
        Worker would reject it with a 400 anyway, the early local
        check saves a round-trip).

        The call is best-effort and **not** retried on failure — a
        missing record is harmless (one fewer data point inside the
        window) and re-trying would race with the lease holder's
        publish/release pipeline.  Callers MUST treat
        :class:`LoginStateUnavailable` as a fail-open signal exactly
        like every other method on this client.
        """
        if not holder_id:
            raise LoginStateUnavailable("holder_id must be a non-empty string")
        if not proxy_name:
            raise LoginStateUnavailable("proxy_name must be a non-empty string")
        if outcome not in ("success", "failure"):
            raise LoginStateUnavailable(
                f"outcome must be 'success' or 'failure'; got {outcome!r}"
            )
        body = {
            "holder_id": holder_id,
            "proxy_name": proxy_name,
            "outcome": outcome,
        }
        resp = self._do_request("POST", "/login_state/record_attempt", body)
        try:
            return RecordAttemptResult(
                recent_attempt_count=int(resp["recent_attempt_count"]),
                recent_failure_count=int(resp["recent_failure_count"]),
                cooldown_until_ms=int(resp.get("cooldown_until_ms", 0) or 0),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed record_attempt response: {_redact_resp(resp)!r} ({e})"
            ) from e

    def release_lease(self, holder_id: str) -> ReleaseLeaseResult:
        """Release the re-login mutex.

        Non-owner releases are silently ignored by the Worker
        (``released:false``); the caller can fire-and-forget without a
        prior ownership check.
        """
        if not holder_id:
            raise LoginStateUnavailable("holder_id must be a non-empty string")
        body = {"holder_id": holder_id}
        resp = self._do_request("POST", "/login_state/release_lease", body)
        try:
            return ReleaseLeaseResult(
                released=bool(resp["released"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LoginStateUnavailable(
                f"malformed release_lease response: {_redact_resp(resp)!r} ({e})"
            ) from e

    # ``health_check``, ``close``, and ``_do_request`` are inherited
    # from :class:`BaseDOClient` and behave identically to the
    # previously-inlined versions.


def create_login_state_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",
) -> Optional[LoginStateClient]:
    """Build a client from env vars, returning ``None`` when disabled.

    Designed to mirror :func:`create_coordinator_from_env` so wiring
    code can decide independently whether the per-proxy throttle and
    the cross-runtime login state are both enabled, without juggling two
    sets of env vars.

    Returns ``None`` when either env var is unset/empty (the supported way
    to disable the cross-runtime login state and fall back to per-runner
    login behaviour).

    Returns ``None`` (and logs an ERROR) when the URL is configured but
    ``/health`` does not respond — surfaces deployment misconfiguration
    early without breaking the spider.
    """
    url = (os.environ.get(url_env) or "").strip()
    token = (os.environ.get(token_env) or "").strip()
    if not url or not token:
        logger.info(
            "Login-state client not configured (%s/%s unset) — "
            "using per-runner login only",
            url_env, token_env,
        )
        return None

    client = LoginStateClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Login-state Worker URL %s is configured but /health did not respond — "
            "falling back to per-runner login for this run",
            url,
        )
        client.close()
        return None
    logger.info("Login-state client initialised: base_url=%s", url)
    return client
