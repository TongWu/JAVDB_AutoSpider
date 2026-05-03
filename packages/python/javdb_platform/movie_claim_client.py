"""HTTP client for the Cloudflare Worker + Durable Object movie claim coordinator.

Sister to :mod:`packages.python.javdb_platform.login_state_client` and
:mod:`packages.python.javdb_platform.proxy_coordinator_client`; targets the
``MovieClaimState`` per-day-sharded Durable Object.

P1-B mutex: when two GH Actions runners would otherwise race to fetch the
same JavDB ``/v/<id>`` detail page, the runner that "wins" the
:meth:`MovieClaimClient.claim` call gets exclusive access until it completes
or releases.  Other runners observe ``acquired=False`` and either back off
(``already_completed=False``) or skip + record local history
(``already_completed=True``).

Design constraints (mirror those of :mod:`proxy_coordinator_client` /
:mod:`login_state_client`):

- One HTTP round-trip per call; **no retries** on failure.  Callers MUST
  treat :class:`MovieClaimUnavailable` as a fail-open signal and fall
  back to the existing per-process dedup / direct fetch.
- A short timeout (default 5 s) bounds the worst-case impact of a slow
  Worker; the spider's hot path is never blocked for more than that.
- The client is thread-safe: each call constructs its own request and the
  underlying ``requests.Session`` only stores connection-pool metadata.

Wire format and field semantics live in
``JAVDB_AutoSpider_Proxycoordinator/src/types.ts`` (the ``MovieClaim*``
type cluster).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-movie-claim-client/1.0"

# Mirror of ``DEFAULT_MOVIE_CLAIM_TTL_MS`` /
# ``MOVIE_CLAIM_TTL_{MIN,MAX}_MS`` on the Worker side.  Exposed here so
# callers can stay within range without an extra round-trip; the Worker
# still clamps defensively if a caller goes over.
DEFAULT_CLAIM_TTL_MS = 30 * 60 * 1000          # 30 min
CLAIM_TTL_MIN_MS = 60_000                       # 1 min
CLAIM_TTL_MAX_MS = 2 * 60 * 60 * 1000           # 2 h


# Operational time zone used for per-day sharding.  Asia/Singapore is
# UTC+08:00 and DST-free, so a fixed-offset tz is exact and matches the
# Worker's ``currentSingaporeDate`` helper without an IANA tz-data
# dependency in either runtime.
_OPS_TZ = timezone(timedelta(hours=8))


def current_shard_date() -> str:
    """Return the per-day shard identifier for "right now" in operations TZ.

    Format mirrors the Worker's ``YYYY-MM-DD``.  Callers that need to claim
    the same href across the day boundary (e.g. an ingestion that spans
    midnight) MUST pin a single shard date at *task dispatch time* and pass
    it explicitly to every claim/release/complete call so the same movie
    always maps to the same shard — calling :func:`current_shard_date` at
    each step would otherwise re-fragment the claim across two shards.
    """
    return datetime.now(_OPS_TZ).strftime("%Y-%m-%d")


class MovieClaimUnavailable(Exception):
    """Raised when the movie claim Worker cannot be reached or returns an error.

    This is a *signal*, not a panic.  Every callsite in the spider treats
    it as "fall back to per-process dedup" — the local in-memory
    ``_completed_entries`` / ``_in_flight`` sets continue to function
    exactly as before, and the worst-case outcome is two runners
    independently fetching the same detail page (the legacy behaviour).
    """


@dataclass(frozen=True)
class ClaimResult:
    """Reply from ``POST /claim_movie``.

    Four exhaustive cases — pattern-match on the booleans + cooldown:

    1. ``acquired=True`` → caller now owns the claim; proceed with fetch.
       Caller MUST eventually call :meth:`complete` (success) or
       :meth:`release` (abort / retry handoff) to free the slot.
       (P2-A: ``fail_count`` may be > 0 in the rare case of a renewal
       mid-failure-window — the previous failures are surfaced for ops
       visibility but do NOT block the renewal.)
    2. ``acquired=False, already_completed=True`` → another runner has
       already finished this href in the same per-day shard; skip + mark
       local history.
    3. ``acquired=False, cooldown_until > server_time_ms`` (P2-A) →
       href is in cooldown after repeated failures.  The caller MUST
       NOT retry before ``cooldown_until``; the back-off is encoded
       server-side (``MOVIE_CLAIM_COOLDOWN_LADDER_MS``).
    4. ``acquired=False, already_completed=False, cooldown_until=0`` →
       another runner is *currently* working on the href; back off and
       retry later (the plan recommends 60–120 s).

    P2-A fields default to ``0`` / empty string when the Worker is on
    a pre-P2-A deploy that omits them — the caller transparently treats
    such a response as "no cooldown info" (= legacy P1-B semantics).
    """

    acquired: bool
    current_holder_id: str
    expires_at: int
    already_completed: bool
    server_time_ms: int
    cooldown_until: int = 0
    last_error_kind: str = ""
    fail_count: int = 0


@dataclass(frozen=True)
class ReleaseResult:
    """Reply from ``POST /release_movie``.

    ``released`` is ``False`` when the caller is not the current holder
    (e.g. its claim already expired and was reclaimed by another runner,
    or another runner already completed the href).  The caller can safely
    treat this as a no-op — the slot is gone either way.
    """

    released: bool
    server_time_ms: int


@dataclass(frozen=True)
class CompleteResult:
    """Reply from ``POST /complete_movie``.

    ``completed`` is ``True`` when the href is now in the shard's
    ``completed[]`` list — either freshly added by this caller or already
    present from a previous successful complete (idempotent).  ``False``
    means a stale-holder complete (the active claim belongs to someone
    else); the caller should typically log + retry from a fresh claim.
    """

    completed: bool
    href: str
    server_time_ms: int


@dataclass(frozen=True)
class StatusResult:
    """Reply from ``GET /movie_status?href=...&date=YYYY-MM-DD``.

    Used for ops debugging only; the spider's hot path never calls this.
    P2-A fields default to ``0`` / empty string for backward-compat
    with pre-P2-A Worker deploys.
    """

    current_holder_id: Optional[str]
    expires_at: int
    already_completed: bool
    server_time_ms: int
    cooldown_until: int = 0
    last_error_kind: str = ""
    fail_count: int = 0


@dataclass(frozen=True)
class ReportFailureResult:
    """Reply from ``POST /report_failure`` (P2-A).

    ``dead_lettered`` is ``True`` once ``fail_count`` crosses the
    server-side dead-letter threshold (default 8); the caller can use
    this to short-circuit further retries for the rest of the shard's
    lifetime.
    """

    fail_count: int
    cooldown_until: int
    dead_lettered: bool
    server_time_ms: int


def _extract_server_time_ms(data: dict) -> int:
    """Read the server-side timestamp from a response.

    Prefers ``server_time_ms`` and falls back to ``server_time`` for parity
    with the Worker (which currently emits the latter from ``Date.now()``
    already in ms).  Mirrors :func:`login_state_client._extract_server_time_ms`
    so the Worker can migrate to the explicit-units key without coordinated
    client deploys.
    """
    if "server_time_ms" in data:
        return int(data["server_time_ms"])
    return int(data["server_time"])


class MovieClaimClient:
    """HTTP client for the MovieClaimState DO.

    Construct once per process and pass into the runtime's detail-fetch
    pipeline.  All four methods are blocking and short-lived; the spider's
    hot path is bounded by ``timeout`` (5 s default) on every call.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``
            (same Worker that hosts the per-proxy throttle + login-state endpoints).
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

    def claim(
        self,
        href: str,
        holder_id: str,
        *,
        ttl_ms: int = DEFAULT_CLAIM_TTL_MS,
        date: Optional[str] = None,
    ) -> ClaimResult:
        """Try to acquire (or renew) the claim on *href* for the current shard.

        ``date`` defaults to "today in Asia/Singapore" via
        :func:`current_shard_date`.  Long-running ingestions MUST pin a
        date at task dispatch time and pass it explicitly; otherwise the
        same href could land in two shards across midnight and lose
        cross-runner exclusivity.

        Returns immediately with a :class:`ClaimResult`; raises
        :class:`MovieClaimUnavailable` on any failure (timeout, non-2xx,
        connection error, malformed response).
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "ttl_ms": int(ttl_ms),
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/claim_movie", body)
        try:
            return ClaimResult(
                acquired=bool(resp["acquired"]),
                current_holder_id=str(resp.get("current_holder_id", "") or ""),
                expires_at=int(resp.get("expires_at", 0) or 0),
                already_completed=bool(resp.get("already_completed", False)),
                server_time_ms=_extract_server_time_ms(resp),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                last_error_kind=str(resp.get("last_error_kind", "") or ""),
                fail_count=int(resp.get("fail_count", 0) or 0),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed claim response: {resp!r} ({e})"
            ) from e

    def release(
        self,
        href: str,
        holder_id: str,
        *,
        date: Optional[str] = None,
    ) -> ReleaseResult:
        """Relinquish a held claim on *href* (e.g. after a fetch failure).

        Non-owner releases are silently ignored by the Worker
        (``released:false``); the caller can fire-and-forget without a
        prior ownership check — the post-condition "this runner no longer
        holds the claim" is true either way.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/release_movie", body)
        try:
            return ReleaseResult(
                released=bool(resp["released"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed release response: {resp!r} ({e})"
            ) from e

    def complete(
        self,
        href: str,
        holder_id: str,
        *,
        date: Optional[str] = None,
    ) -> CompleteResult:
        """Mark *href* as fully processed for this shard.

        Subsequent claims on the same href in the same shard return
        ``acquired=False, already_completed=True`` so peer runners can
        skip the fetch entirely.  Idempotent: repeated completes return
        ``completed=True`` even from non-holders.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/complete_movie", body)
        try:
            return CompleteResult(
                completed=bool(resp["completed"]),
                href=str(resp.get("href", href)),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed complete response: {resp!r} ({e})"
            ) from e

    def report_failure(
        self,
        href: str,
        holder_id: str,
        *,
        error_kind: str = "",
        cooldown_ms: Optional[int] = None,
        date: Optional[str] = None,
    ) -> ReportFailureResult:
        """Record a failure on *href* (P2-A).

        Bumps the per-href ``fail_count`` and computes the next
        ``cooldown_until`` according to the server's cooldown ladder
        (``MOVIE_CLAIM_COOLDOWN_LADDER_MS``).  Pass ``cooldown_ms`` to
        override the ladder for one specific failure (e.g. a
        well-understood transient like ``proxy_timeout`` deserving a
        shorter cooldown than a generic ``http_500``).

        Side effect: if *holder_id* still owns the active claim on
        ``href``, the DO releases it as part of the failure report —
        symmetric with the success-path :meth:`complete`, so peers
        observe the slot as free without an extra :meth:`release` call.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "error_kind": error_kind,
            "date": date or current_shard_date(),
        }
        if cooldown_ms is not None:
            body["cooldown_ms"] = int(cooldown_ms)
        resp = self._do_request("POST", "/report_failure", body)
        try:
            return ReportFailureResult(
                fail_count=int(resp["fail_count"]),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                dead_lettered=bool(resp.get("dead_lettered", False)),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed report_failure response: {resp!r} ({e})"
            ) from e

    def get_status(
        self,
        href: str,
        *,
        date: Optional[str] = None,
    ) -> StatusResult:
        """Look up the current state of *href*.  Debug / ops only.

        The spider's hot path uses :meth:`claim` (which already returns
        ``already_completed`` + ``current_holder_id`` in one round-trip);
        :meth:`get_status` exists for the rare case where ops want to
        inspect a shard's state without trying to claim anything.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        target_date = date or current_shard_date()
        path = f"/movie_status?href={requests.utils.quote(href, safe='')}&date={target_date}"
        resp = self._do_request("GET", path)
        try:
            holder = resp.get("current_holder_id")
            return StatusResult(
                current_holder_id=str(holder) if holder else None,
                expires_at=int(resp.get("expires_at", 0) or 0),
                already_completed=bool(resp.get("already_completed", False)),
                server_time_ms=_extract_server_time_ms(resp),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                last_error_kind=str(resp.get("last_error_kind", "") or ""),
                fail_count=int(resp.get("fail_count", 0) or 0),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed status response: {resp!r} ({e})"
            ) from e

    def health_check(self) -> bool:
        """Return ``True`` if ``GET /health`` returns 200.

        Reuses the unauthenticated liveness probe shared with the proxy
        coordinator and login-state DOs (they all live behind the same
        Worker), so a single ``/health`` call validates that the new
        ``/claim_movie`` etc. routes are reachable.  Never raises.
        """
        try:
            resp = self._session.get(f"{self._base_url}/health", timeout=self._timeout)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        """Release the underlying ``requests.Session``.  Idempotent."""
        try:
            self._session.close()
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
            logger.warning("Failed to close movie-claim HTTP session: %s", exc)

    # -- internals ---------------------------------------------------------

    def _do_request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
    ) -> dict:
        """Issue a single HTTP call and decode its JSON body.

        All four exception paths (timeout, connection error, non-2xx,
        malformed JSON) collapse into :class:`MovieClaimUnavailable` so
        callers only have to handle one type.  Never retries.
        """
        url = f"{self._base_url}{path}"
        try:
            if method == "GET":
                resp = self._session.get(url, timeout=self._timeout)
            else:
                resp = self._session.post(url, json=body or {}, timeout=self._timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise MovieClaimUnavailable(f"network error: {e}") from e
        except requests.RequestException as e:
            raise MovieClaimUnavailable(f"request failed: {e}") from e

        if resp.status_code >= 300:
            # 503 here typically means "MOVIE_CLAIM_DO binding missing" —
            # i.e. the v3 migration hasn't been applied yet.  Surfacing the
            # status in the message lets the operator notice & deploy.
            raise MovieClaimUnavailable(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as e:
            raise MovieClaimUnavailable(f"invalid JSON: {e}") from e


def create_movie_claim_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",
    enabled_env: str = "MOVIE_CLAIM_ENABLED",
) -> Optional[MovieClaimClient]:
    """Build a client from env vars, returning ``None`` when disabled.

    Three independent disable paths, all returning ``None`` so the spider
    transparently falls back to its pre-DO behaviour:

    - ``MOVIE_CLAIM_ENABLED`` is unset / not in ``{"1", "true", "yes"}``
      (default OFF — single-runner deployments pay zero claim overhead);
    - either of ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN``
      is empty (the supported way to disable *all* coordinator features);
    - the URL is configured but ``/health`` does not respond (logs an
      ERROR so deployment misconfiguration surfaces early).

    Designed to mirror :func:`create_login_state_client_from_env` so
    wiring code can decide independently whether the per-proxy throttle,
    cross-runtime login state, and movie-claim coordinator are each
    enabled — without juggling three sets of env vars.
    """
    raw_enabled = (os.environ.get(enabled_env) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes"}:
        logger.info(
            "Movie-claim client disabled (%s=%r) — using per-process dedup only",
            enabled_env, os.environ.get(enabled_env, ""),
        )
        return None

    url = (os.environ.get(url_env) or "").strip()
    token = (os.environ.get(token_env) or "").strip()
    if not url or not token:
        logger.info(
            "Movie-claim client not configured (%s/%s unset) — "
            "using per-process dedup only",
            url_env, token_env,
        )
        return None

    client = MovieClaimClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Movie-claim Worker URL %s is configured but /health did not respond — "
            "falling back to per-process dedup for this run",
            url,
        )
        client.close()
        return None
    logger.info("Movie-claim client initialised: base_url=%s", url)
    return client
