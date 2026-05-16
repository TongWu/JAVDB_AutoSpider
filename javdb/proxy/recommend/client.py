"""W6.B (W5.5) — Python client for the Worker's /recommend_proxy aggregator.

The Worker fans out to every ProxyCoordinator DO listed in ``proxy_ids``,
reads each DO's most-recent health snapshot, and returns the IDs ranked
by score. This client is a thin GET wrapper around that route.

Pairs with :class:`RecommendProxyPolicy` (sibling module), which wraps
this client in a TTL-cached, background-refreshing health provider
callable that :class:`ProxyPool.set_health_provider` already accepts.
The split keeps the HTTP surface and the caching policy in different
files so each can be tested in isolation.

Design mirrors the four sibling DO clients:

- Subclasses :class:`BaseDOClient`; ``_unavailable_exc`` set to the
  domain-specific exception so callers handle one type.
- Fail-open factory ``create_recommend_proxy_client_from_env()``: any
  configuration miss (flag off, URL/token missing, /health failing)
  returns ``None`` and the runtime keeps its existing local
  ``coord.get_proxy_health_score`` provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

from javdb.infra import config as config_helper
from javdb.proxy.coordinator.do_client_base import (
    BaseDOClient,
    DOClientUnavailable,
)
from javdb.infra.logging import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-recommend-proxy-client/1.0"
_DEFAULT_TOP_N_CAP = 32  # Worker enforces this server-side too.


class RecommendProxyUnavailable(DOClientUnavailable):
    """Raised when the recommend-proxy aggregator cannot be reached.

    Pure fail-open signal: every callsite in the spider treats it as
    "fall back to the existing local health provider" — never a fatal
    error. Mirrors the other ``*Unavailable`` types in this package.
    """


@dataclass(frozen=True)
class ProxyRecommendation:
    """One entry in the ranked output of :meth:`RecommendProxyClient.recommend`.

    Field semantics mirror the Worker's :type:`SignalsResponse`
    ``recommendations[]`` (see ``proxy-coordinator/src/index.ts``).
    """

    proxy_id: str
    score: float
    latency_ema_ms: float
    success_count: int
    failure_count: int
    banned: bool
    requires_cf_bypass: bool
    available: bool


@dataclass(frozen=True)
class RecommendResult:
    """Reply from ``GET /recommend_proxy``."""

    recommendations: List[ProxyRecommendation] = field(default_factory=list)
    queried_proxy_ids: List[str] = field(default_factory=list)
    server_time_ms: int = 0


def _parse_recommendation(payload: Any) -> Optional[ProxyRecommendation]:
    """Decode one ``recommendations[i]`` row defensively.

    Returns ``None`` on any structural / type error so a single malformed
    entry doesn't fail the whole payload. Numeric coercions cast through
    ``float(...)`` / ``int(...)`` so the Worker can send strings or
    numbers interchangeably without breaking the client.
    """
    if not isinstance(payload, dict):
        return None
    try:
        proxy_id = str(payload.get("proxy_id", ""))
        if not proxy_id:
            return None
        return ProxyRecommendation(
            proxy_id=proxy_id,
            score=float(payload.get("score", 0.5)),
            latency_ema_ms=float(payload.get("latency_ema_ms", 0.0)),
            success_count=int(payload.get("success_count", 0) or 0),
            failure_count=int(payload.get("failure_count", 0) or 0),
            banned=bool(payload.get("banned", False)),
            requires_cf_bypass=bool(payload.get("requires_cf_bypass", False)),
            available=bool(payload.get("available", True)),
        )
    except (TypeError, ValueError):
        return None


class RecommendProxyClient(BaseDOClient):
    """HTTP client for the proxy-coordinator's W5.5 /recommend_proxy aggregator.

    ``base_url`` / ``token`` mirror the other DO clients; the same
    ``PROXY_COORDINATOR_URL`` + ``PROXY_COORDINATOR_TOKEN`` secrets are
    reused. There is no per-proxy DO addressing — this client always
    talks to the Worker-level aggregator route.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``.
        token: Bearer token (must match ``PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    _unavailable_exc = RecommendProxyUnavailable

    def recommend(
        self,
        proxy_ids: List[str],
        *,
        top_n: Optional[int] = None,
        include_unhealthy: bool = False,
    ) -> RecommendResult:
        """Fetch a ranked recommendation for the supplied proxy IDs.

        Empty ``proxy_ids`` short-circuits to an empty result without
        hitting the network — the Worker also returns an empty list in
        that case but the early return shaves a round-trip for the
        steady-state "no proxies configured yet" path.

        ``top_n`` caps the result list length (server caps at 32 too).
        ``include_unhealthy=True`` keeps banned proxies in the response
        ranked last; default omits them so the caller can blindly take
        ``recommendations[0]``.

        Returns a fully-populated :class:`RecommendResult`. Raises
        :class:`RecommendProxyUnavailable` on any network / decode
        failure.
        """
        if not proxy_ids:
            return RecommendResult()

        cleaned = [pid.strip() for pid in proxy_ids if isinstance(pid, str) and pid.strip()]
        if not cleaned:
            return RecommendResult()
        # Cap client-side too so a misconfigured caller doesn't generate
        # a URL longer than the Worker would honour.
        cleaned = cleaned[:_DEFAULT_TOP_N_CAP]

        query = ["proxy_ids=" + ",".join(cleaned)]
        if top_n is not None and top_n > 0:
            query.append(f"top_n={int(top_n)}")
        if include_unhealthy:
            query.append("include_unhealthy=1")
        path = "/recommend_proxy?" + "&".join(query)
        resp = self._do_request("GET", path)

        # Decode defensively — drop malformed rows rather than fail the
        # whole payload. This mirrors the fail-open contract of the
        # other DO clients and lets us keep returning a usable cache
        # even when a Worker upgrade adds a field this client doesn't
        # know about.
        raw_recs = resp.get("recommendations") or []
        if not isinstance(raw_recs, list):
            raise RecommendProxyUnavailable(
                f"recommendations must be a list, got {type(raw_recs).__name__}",
            )
        decoded: List[ProxyRecommendation] = []
        for entry in raw_recs:
            rec = _parse_recommendation(entry)
            if rec is not None:
                decoded.append(rec)

        queried = resp.get("queried_proxy_ids") or []
        if not isinstance(queried, list):
            queried = []
        return RecommendResult(
            recommendations=decoded,
            queried_proxy_ids=[str(x) for x in queried],
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )


def create_recommend_proxy_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",  # noqa: S107
    enabled_env: str = "RECOMMEND_PROXY_ENABLED",
) -> Optional[RecommendProxyClient]:
    """Build a client from env vars + cfg, returning ``None`` when disabled.

    Three independent disable paths, identical to the other DO factories
    so wiring code can flip features on/off independently:

    - ``RECOMMEND_PROXY_ENABLED`` is not in ``{"1", "true", "yes"}``
      (default OFF — existing deployments pay zero overhead);
    - either of ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN``
      is empty;
    - the URL is configured but ``/health`` does not respond.

    The enabled flag is read from the process environment (so a single
    `wrangler.toml` op flip doesn't require a redeploy on the runner
    side); URL + token reuse the shared coordinator pair via
    :mod:`config_helper`.
    """
    raw_enabled = (os.environ.get(enabled_env) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes"}:
        logger.info(
            "RecommendProxy client disabled (%s=%r) — using local health provider",
            enabled_env, os.environ.get(enabled_env, ""),
        )
        return None

    url = (config_helper.cfg(url_env, "") or "").strip()
    token = (config_helper.cfg(token_env, "") or "").strip()
    if not url or not token:
        logger.info(
            "RecommendProxy client not configured (%s/%s missing)",
            url_env, token_env,
        )
        return None

    client = RecommendProxyClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "RecommendProxy Worker URL %s is configured but /health did not respond — "
            "falling back to local health provider for this run",
            url,
        )
        client.close()
        return None
    logger.info("RecommendProxy client initialised: base_url=%s", url)
    return client
