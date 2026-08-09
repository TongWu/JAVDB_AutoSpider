"""Best-effort publish of a freshly-obtained JavDB session cookie to the
``GlobalLoginState`` Durable Object, so GitHub Actions runners can adopt the
session (via ``FetchEngine._inherit_login_state`` / the login poller) and skip
their own — currently Cloudflare-blocked — login.

The coordinator is a *cache populated only by a successful in-pipeline login*.
When every CI login fails, nothing is ever published and each run re-logins
from scratch. Running ``apps.cli.login`` locally (where the operator can solve
the captcha / use an unflagged IP) and publishing the result here warms that
cache without depending on CI succeeding first.

Kept in its own module — not in ``login.py`` — so it imports cleanly under
pytest: ``login.py`` runs an import-time credential check that ``sys.exit``s
when ``config.py`` is absent.
"""

from __future__ import annotations

import uuid

from javdb.infra.config import cfg
from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# The lease must outlive the publish round-trip; mirror ``LoginCoordinator``'s
# ``_DO_LEASE_TTL_MS``. We release it immediately after publishing, so this is
# only an upper bound on how long a crashed CLI could hold the mutex.
_LEASE_TTL_MS = 60_000


def publish_login_state(session_cookie: str, proxy_name: str) -> bool:
    """Broadcast *session_cookie* (bound to *proxy_name*) to the coordinator.

    Returns ``True`` only when the cookie was published. Every failure mode —
    coordinator unconfigured/unreachable, lease held by a peer runner, or any
    Durable Object error — is swallowed and returns ``False``: the caller's
    local login (and ``config.py`` update) already succeeded and must never be
    failed by best-effort bookkeeping.

    ``proxy_name`` should name a proxy that also exists in the consuming
    runner's ``PROXY_POOL`` (the configured ``LOGIN_PROXY_NAME`` does), so the
    adopting runner can bind the cookie to the matching worker. A name with no
    match downstream still lands the cookie in the runner's login state but
    will not be bound to a specific worker.
    """
    # Imported lazily so this module's import cost stays at ``cfg`` + logging.
    from javdb.proxy.coordinator.login_state_client import (
        LoginStateClient,
        LoginStateUnavailable,
    )

    if not session_cookie or not proxy_name:
        return False

    # cfg() is intentionally untyped; coerce to str so a misconfigured
    # non-string value can't raise AttributeError before the try below and
    # break the fail-open contract.
    url = str(cfg('PROXY_COORDINATOR_URL', '') or '').strip()
    token = str(cfg('PROXY_COORDINATOR_TOKEN', '') or '').strip()
    if not url or not token:
        logger.info(
            "Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN "
            "unset) — skipping login-state publish",
        )
        return False

    holder_id = f"login-cli-{uuid.uuid4().hex[:16]}"
    client = None
    try:
        client = LoginStateClient(base_url=url, token=token)
        if not client.health_check():
            logger.warning(
                "Proxy coordinator /health did not respond — skipping "
                "login-state publish",
            )
            return False
        # ``publish`` requires holding the re-login mutex (the Worker returns
        # 409 ``lease_required`` otherwise); acquire it for this proxy, publish,
        # then release promptly so peer runners aren't blocked for the TTL.
        lease = client.acquire_lease(holder_id, proxy_name, _LEASE_TTL_MS)
        if not lease.acquired:
            logger.warning(
                "Login-state lease currently held by %s — skipping publish "
                "(the holder will broadcast its own cookie)",
                lease.holder_id,
            )
            return False
        try:
            result = client.publish(holder_id, proxy_name, session_cookie)
        finally:
            try:
                client.release_lease(holder_id)
            except Exception:  # noqa: BLE001 — release is best-effort
                pass
        logger.info(
            "Published login state to coordinator: proxy=%s, version=%d",
            proxy_name, result.version,
        )
        # A consuming runner binds the shared cookie to the worker whose
        # proxy name matches; no pooled worker is named after the direct
        # sentinel, so this publish lands the cookie but it will not bind on
        # CI. Make that loud — the operator should log in through a pooled
        # proxy (set LOGIN_PROXY_NAME) to actually warm CI.
        from javdb.spider.fetch.session import DIRECT_LOGIN_PROXY_NAME

        if proxy_name == DIRECT_LOGIN_PROXY_NAME:
            logger.warning(
                "Published under proxy name %r, which matches no pooled "
                "worker — CI runners will adopt the cookie into login state "
                "but not bind it to a worker, so login-required pages still "
                "re-login. Set LOGIN_PROXY_NAME to a PROXY_POOL proxy and log "
                "in again to bind it.",
                proxy_name,
            )
        return True
    except LoginStateUnavailable as exc:
        logger.warning(
            "Failed to publish login state (proxy=%s): %s — other runners may "
            "re-login independently",
            proxy_name, exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — never break the login CLI
        logger.warning(
            "Unexpected error publishing login state (proxy=%s): %s",
            proxy_name, exc, exc_info=True,
        )
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
