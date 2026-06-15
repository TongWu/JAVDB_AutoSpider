from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError, version as pkg_version
from typing import Literal, cast

from fastapi import APIRouter, Depends

from apps.api.infra.auth import _require_auth
from apps.api.schemas.capabilities_payloads import (
    Build,
    CapabilitiesResponse,
    Features,
    GhActions,
)


def _get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def _backend_version() -> str:
    try:
        return pkg_version("javdb-autospider")
    except PackageNotFoundError:
        return os.getenv("BACKEND_VERSION", "0.0.0-dev")


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _closed_loop_enabled() -> bool:
    """True when the ADR-033 AcquisitionOutcome table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import OPERATIONS_DB_PATH, get_db
        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM AcquisitionOutcome LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _library_ownership_enabled() -> bool:
    """True when the ADR-034 OwnershipLedger table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import OPERATIONS_DB_PATH, get_db
        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM OwnershipLedger LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _library_consumption_enabled() -> bool:
    """True when the ADR-034 ConsumptionSignal table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import OPERATIONS_DB_PATH, get_db
        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM ConsumptionSignal LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _watch_intent_enabled() -> bool:
    """True when the ADR-054 WatchIntent table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import HISTORY_DB_PATH, get_db
        with get_db(HISTORY_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM WatchIntent LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _content_filter_enabled() -> bool:
    """True when the ADR-040 ContentFilterRule table is queryable in REPORTS_DB
    (capability honesty). NOTE: REPORTS_DB, not HISTORY_DB — distinct from
    watch_intent above."""
    try:
        from javdb.storage.db import REPORTS_DB_PATH, get_db
        with get_db(REPORTS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM ContentFilterRule LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _magnet_aggregation_enabled() -> bool:
    """True when at least one indexer source is configured (ADR-054 WS3, capability honesty).

    v1 is ephemeral (no cache table) so there is nothing to probe; the flag is
    config-presence -- MAGNET_SOURCES non-empty -- via the dispatcher's parser.
    """
    try:
        from javdb.integrations.indexer.dispatch import active_sources

        return bool(active_sources())
    except Exception:
        return False


def _subscriptions_enabled() -> bool:
    """True when the ADR-054 ActorSubscription table is queryable."""
    try:
        from javdb.storage.db import HISTORY_DB_PATH, get_db
        with get_db(HISTORY_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM ActorSubscription LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def build_capabilities() -> CapabilitiesResponse:
    ingestion_mode = cast(
        "Literal['local', 'github', 'dual']",
        os.getenv("INGESTION_MODE", "local"),
    )
    storage_backend = cast(
        "Literal['sqlite', 'd1', 'dual']",
        os.getenv("STORAGE_BACKEND", "sqlite"),
    )
    deployment = cast(
        "Literal['colocated', 'split', 'unknown']",
        os.getenv("DEPLOYMENT", "unknown"),
    )

    return CapabilitiesResponse(
        version="2.0.0",
        ingestion_mode=ingestion_mode,
        gh_actions=GhActions(
            tier=cast(
                "Literal['none', 'monitor', 'edit', 'admin']",
                os.getenv("GH_ACTIONS_TIER", "none"),
            ),
            repo=os.getenv("GH_ACTIONS_REPO") or None,
            token_configured=bool(os.getenv("GH_ACTIONS_TOKEN")),
        ),
        storage_backend=storage_backend,
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
            closed_loop=_closed_loop_enabled(),
            library_ownership=_library_ownership_enabled(),
            library_consumption=_library_consumption_enabled(),
            watch_intent=_watch_intent_enabled(),
            content_filter=_content_filter_enabled(),
            magnet_aggregation=_magnet_aggregation_enabled(),
            subscriptions=_subscriptions_enabled(),
            # ADR-035: site-contract drift sentinel ships with the system; the
            # frontend hides the drift panel only when explicitly disabled.
            site_drift_sentinel=_bool_env("FEATURE_SITE_DRIFT_SENTINEL", default=True),
        ),
        deployment=deployment,
        build=Build(
            frontend_version=os.getenv("FRONTEND_VERSION"),
            backend_version=_backend_version(),
            git_sha=_get_git_sha(),
        ),
    )


router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities(_user=Depends(_require_auth)) -> CapabilitiesResponse:
    return build_capabilities()
