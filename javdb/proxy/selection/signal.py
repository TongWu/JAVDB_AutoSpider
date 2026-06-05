"""ADR-023 Phase 4 — :class:`ProxySelectionSignal`, a fail-open health
provider chain for :meth:`ProxyPool.set_health_provider`.

The signal threads a *primary* provider (the W5.5 ``/recommend_proxy``
policy) in front of a *fallback* callable (the local coordinator health
cache) so the runtime always has a single ``Callable[[str],
Optional[float]]`` to hand the pool, regardless of which sources are
live.

Two invariants make this safe to drop into the pool's hot selection
path:

- **Fail-open.** :meth:`score_for` never raises into the pool. A primary
  that returns ``None`` *or raises* hands off to the fallback; a fallback
  that returns ``None`` *or raises* yields ``None``, which the pool reads
  as "use the neutral 0.5 baseline".
- **Raw passthrough.** The signal never clamps, never rejects ``NaN``,
  and never substitutes ``0.5`` for a missing score. ``ProxyPool`` (Rust)
  owns clamp / NaN normalization in ``_safe_health_score``; duplicating
  it here would double-normalize and diverge from production.

The signal only consumes the existing ``score`` value (via
``primary.score_for`` / the fallback callable). It deliberately does
*not* read ``model_score`` / ``rank_score`` / ``confidence`` /
``reason_code`` — those are Worker-side shadow fields the Python pool
does not act on.
"""

from __future__ import annotations

from typing import Callable, Optional

from javdb.infra.logging import get_logger

logger = get_logger(__name__)


class ProxySelectionSignal:
    """Fail-open primary→fallback health-score chain for ``ProxyPool``.

    Args:
        primary: Optional object exposing ``score_for(proxy_name) ->
            Optional[float]`` plus optional ``start()`` and
            ``close()``-or-``shutdown()`` lifecycle hooks and an optional
            ``label`` attribute. In production this is a
            :class:`~javdb.proxy.recommend.policy.RecommendProxyPolicy`.
        fallback_score_for: Optional ``Callable[[str], Optional[float]]``
            consulted when the primary returns ``None`` or raises. In
            production this is
            ``ProxyCoordinatorClient.get_proxy_health_score``.
        label: Optional human-readable description of the active chain
            (e.g. ``"recommend_proxy+coordinator_health"``). When omitted,
            a deterministic label is derived from the configured sources.
    """

    def __init__(
        self,
        *,
        primary: object | None = None,
        fallback_score_for: Callable[[str], Optional[float]] | None = None,
        label: str | None = None,
    ) -> None:
        self._primary = primary
        self._fallback_score_for = fallback_score_for
        self.label = label if label is not None else self._derive_label()

    # -- hot path ----------------------------------------------------------

    def score_for(self, proxy_name: str) -> Optional[float]:
        """Return a raw health score for *proxy_name*, failing open.

        Exact chain (no clamping, no NaN rejection, no None→0.5):

        1. If a primary is configured, call ``primary.score_for``.
        2. If that returns non-``None``, return it UNCHANGED.
        3. If it returns ``None`` or raises, consult the fallback
           callable when one is configured.
        4. If the fallback returns a value, return it UNCHANGED.
        5. If there is no fallback, or it raises, return ``None``.
        """
        if self._primary is not None:
            try:
                primary_score = self._primary.score_for(proxy_name)
            except Exception:  # noqa: BLE001 — must never raise into the pool
                logger.debug(
                    "Primary health provider raised for %r; falling back",
                    proxy_name,
                    exc_info=True,
                )
                primary_score = None
            if primary_score is not None:
                return primary_score

        if self._fallback_score_for is not None:
            try:
                return self._fallback_score_for(proxy_name)
            except Exception:  # noqa: BLE001 — fail open to None
                logger.debug(
                    "Fallback health provider raised for %r; returning None",
                    proxy_name,
                    exc_info=True,
                )
                return None

        return None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the primary's background refresh, if it has one.

        No-op when there is no primary or it lacks ``start()``. Fails open
        (logs rather than raising) so bootstrap can't crash on a flaky
        provider.
        """
        start = getattr(self._primary, "start", None)
        if callable(start):
            try:
                start()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Primary health provider start() failed", exc_info=True,
                )

    def close(self) -> None:
        """Tear down the primary, preferring ``close()`` then ``shutdown()``.

        ``RecommendProxyPolicy`` exposes ``shutdown()`` (not ``close()``),
        so both names are probed. No-op when neither exists. Fails open so
        cleanup paths (atexit, finally blocks) never raise.
        """
        closer = getattr(self._primary, "close", None)
        if not callable(closer):
            closer = getattr(self._primary, "shutdown", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Primary health provider close()/shutdown() failed",
                    exc_info=True,
                )

    # -- internals ---------------------------------------------------------

    def _derive_label(self) -> str:
        """Build a deterministic label from the configured sources.

        Uses the primary's own ``label`` when present, otherwise the class
        name, joined with a fallback marker when a fallback is configured.
        """
        if self._primary is not None:
            primary_label = getattr(self._primary, "label", None)
            if not primary_label:
                primary_label = type(self._primary).__name__
        else:
            primary_label = None

        parts = [p for p in (primary_label, "fallback" if self._fallback_score_for else None) if p]
        return "+".join(parts) if parts else "none"

    # -- production seam ---------------------------------------------------

    @classmethod
    def from_runtime_config(
        cls,
        *,
        proxy_ids: list[str] | None = None,
        coordinator: object | None = None,
    ) -> "Optional[ProxySelectionSignal]":
        """Build the production chain, mirroring ``setup_proxy_pool()``.

        Mirrors ``javdb.spider.runtime.context.RuntimeContext.setup_proxy_pool``:

        - Primary: ``create_recommend_proxy_client_from_env()`` (which
          owns the ``RECOMMEND_PROXY_ENABLED`` flag, URL/token sourcing,
          and the ``/health`` probe) wrapped in a
          :class:`RecommendProxyPolicy` with its default refresh interval
          / stale TTL / ``include_unhealthy`` — the same construction the
          as-built wiring uses (no overrides). ``proxy_ids`` are the
          ``PROXY_POOL`` entry names, exactly as ``setup_proxy_pool``
          derives them.
        - Fallback: ``coordinator.get_proxy_health_score`` when a
          coordinator client is supplied.

        Returns ``None`` when **neither** source is meaningful (no
        recommend client AND no coordinator). This is the smallest
        behavior-preserving shape: ``setup_proxy_pool`` only installs a
        health provider when at least one source exists, otherwise the
        pool stays on round-robin — returning ``None`` here lets the
        caller skip ``set_health_provider`` entirely and preserve that
        exact behavior. A signal with only a fallback is returned when the
        recommend client is unavailable but a coordinator exists (this
        reproduces the as-built fallback-to-coordinator path).

        Note: the caller owns lifecycle (``start()`` after construction,
        ``close()`` at teardown) and ``set_health_provider`` wiring;
        this factory does not start the policy or register atexit hooks.
        """
        from javdb.proxy.recommend.client import (
            create_recommend_proxy_client_from_env,
        )
        from javdb.proxy.recommend.policy import RecommendProxyPolicy

        fallback = None
        if coordinator is not None:
            fallback = getattr(coordinator, "get_proxy_health_score", None)

        # Recommend (primary) setup is best-effort: a failure here must
        # not strip the coordinator fallback and drop the run all the way
        # to round-robin. Catch it and fall through with no primary so a
        # fallback-only signal can still be built when a coordinator is
        # present — preserving the documented primary→fallback→None chain.
        primary = None
        primary_label = None
        try:
            rec_client = create_recommend_proxy_client_from_env()
            if rec_client is not None:
                primary = RecommendProxyPolicy(
                    rec_client, proxy_ids=list(proxy_ids or []),
                )
                primary_label = "recommend_proxy"
        except Exception:  # noqa: BLE001 — preserve coordinator fallback
            logger.warning(
                "RecommendProxy setup failed; falling back to "
                "coordinator health if available",
                exc_info=True,
            )

        if primary is None and fallback is None:
            return None

        label_parts = [p for p in (primary_label, "coordinator_health" if fallback else None) if p]
        label = "+".join(label_parts) if label_parts else None

        return cls(primary=primary, fallback_score_for=fallback, label=label)
