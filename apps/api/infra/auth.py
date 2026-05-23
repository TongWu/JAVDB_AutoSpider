"""Auth dependency and token helpers."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Request
from passlib.context import CryptContext

from apps.api.services import context
from javdb.infra.config import cfg

PASSWORD_CTX = CryptContext(schemes=["bcrypt"], deprecated="auto")

_RUNTIME_ENV = os.getenv("ENVIRONMENT", os.getenv("FLASK_ENV", "")).strip().lower()
_IS_PRODUCTION_ENV = _RUNTIME_ENV == "production"

# Path to the API config override store (reports/api_config_store.json).
# This is the same file written by config_service.save_store() — kept as a
# module-level constant so tests can monkeypatch it.
_STORE_PATH = Path(__file__).resolve().parents[3] / "reports" / "api_config_store.json"


def _read_store_value(name: str) -> str | None:
    """Read a single key from the API config override store, if it exists.

    Returns ``None`` when the store is absent, unreadable, or lacks *name*.
    The store may contain encrypted blobs for sensitive keys — those are
    returned as-is (a raw ``{"enc": "..."}`` dict), which the caller treats
    as absent since they cannot be decrypted without the Fernet key.  In
    practice this only matters for SENSITIVE_KEYS like QB_PASSWORD; the auth
    fields (ADMIN_PASSWORD, API_SECRET_KEY, …) are stored as plaintext in the
    store by config_service.save_store() when no encryption key is configured.
    """
    try:
        if not _STORE_PATH.exists():
            return None
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    val = data.get(name)
    if val is None:
        return None
    # Encrypted blobs are dicts with an "enc" key; we cannot decrypt them here
    # (no Fernet instance), so treat them as absent.
    if isinstance(val, dict):
        return None
    return str(val)


def _resolve(name: str, default: str = "") -> str:
    """Return *name* using precedence: env > config.py > override store > default.

    - Empty env values fall through (unset and empty are treated the same).
    - config.py is the operator-owned authoritative source for local deploys.
    - The override store (reports/api_config_store.json) captures values set
      via PUT /api/config or POST /api/explore/sync-cookie so that wizard
      changes are picked up after a BE restart without touching config.py.
    """
    env_val = os.getenv(name)
    if env_val is not None and env_val.strip():
        return env_val.strip()
    cfg_val = cfg(name, "")
    cfg_str = str(cfg_val).strip() if cfg_val is not None else ""
    if cfg_str:
        return cfg_str
    store_val = _read_store_value(name)
    if store_val is not None and store_val.strip():
        return store_val.strip()
    return default


API_SECRET_KEY = _resolve("API_SECRET_KEY", "")
if not API_SECRET_KEY:
    if _IS_PRODUCTION_ENV:
        raise RuntimeError("API_SECRET_KEY is required.")
    API_SECRET_KEY = secrets.token_urlsafe(48)
    context.logger.warning(
        "API_SECRET_KEY missing in non-production; generated ephemeral secret for this process."
    )
if len(API_SECRET_KEY) < 32:
    message = "API_SECRET_KEY must be at least 32 characters long."
    if _IS_PRODUCTION_ENV:
        raise RuntimeError(message)
    context.logger.warning("%s Running in non-production mode.", message)

ACCESS_TOKEN_EXPIRE_SECONDS = int(_resolve("ACCESS_TOKEN_EXPIRE_SECONDS", "1800") or "1800")
REFRESH_TOKEN_EXPIRE_SECONDS = int(
    _resolve("REFRESH_TOKEN_EXPIRE_SECONDS", str(7 * 24 * 3600)) or str(7 * 24 * 3600)
)
MAX_SESSIONS_PER_USER = int(_resolve("MAX_SESSIONS_PER_USER", "3") or "3")

ACTIVE_TOKENS: Dict[str, list[tuple[str, int]]] = {}
REVOKED_JTI: Dict[str, int] = {}
RATE_BUCKETS: Dict[str, list[float]] = {}
_AUTH_LOCK = threading.Lock()

METHOD_LIMITS = {
    "/api/auth/login": (5, 60, "ip"),
    "/api/auth/change-password": (5, 60, "user"),
    "/api/tasks/daily": (10, 60, "user"),
    "/api/tasks/adhoc": (10, 60, "user"),
    "/api/config": (20, 60, "user"),
    "/api/config/meta": (60, 60, "user"),
}


def _hash_password(plain: str) -> str:
    return PASSWORD_CTX.hash(plain)


ADMIN_USERNAME = _resolve("ADMIN_USERNAME", "admin") or "admin"
ADMIN_PASSWORD_HASH = _resolve("ADMIN_PASSWORD_HASH") or None
if not ADMIN_PASSWORD_HASH:
    admin_password = _resolve("ADMIN_PASSWORD")
    if not admin_password:
        if _IS_PRODUCTION_ENV:
            raise RuntimeError(
                "ADMIN_PASSWORD_HASH or ADMIN_PASSWORD must be provided."
            )
        admin_password = secrets.token_urlsafe(24)
        context.logger.warning(
            "ADMIN_PASSWORD_HASH/ADMIN_PASSWORD missing in non-production; "
            "generated ephemeral admin password for this process."
        )
    ADMIN_PASSWORD_HASH = _hash_password(admin_password)

READONLY_USERNAME = _resolve("READONLY_USERNAME", "readonly") or "readonly"
READONLY_PASSWORD_HASH = _resolve("READONLY_PASSWORD_HASH") or None
if not READONLY_PASSWORD_HASH:
    readonly_password = _resolve("READONLY_PASSWORD")
    if readonly_password:
        context.logger.warning(
            "READONLY_PASSWORD is provided in plaintext and will be hashed at startup."
        )
        READONLY_PASSWORD_HASH = _hash_password(readonly_password)

USERS = {
    ADMIN_USERNAME: {"role": "admin", "password_hash": ADMIN_PASSWORD_HASH},
}
if READONLY_PASSWORD_HASH:
    USERS[READONLY_USERNAME] = {
        "role": "readonly",
        "password_hash": READONLY_PASSWORD_HASH,
    }


def _prune_revoked_jti(now: Optional[int] = None) -> None:
    # Caller is expected to hold ``_AUTH_LOCK`` (see ``_prune_sessions``
    # and ``_jwt_decode``); doc this in the body since the lock-free
    # implementation here would otherwise race against concurrent
    # mutations of REVOKED_JTI from a peer worker thread.
    ts = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    expired = [jti for jti, exp in REVOKED_JTI.items() if exp <= ts]
    for jti in expired:
        REVOKED_JTI.pop(jti, None)


def _prune_sessions(username: str) -> list[tuple[str, int]]:
    """Drop expired / revoked sessions for *username* and return the rest.

    Caller MUST hold ``_AUTH_LOCK``. Both production callsites
    (``login_payload`` / ``refresh_token_payload`` in
    ``apps/api/services/auth_service.py``) already wrap the call in
    ``with auth_infra._AUTH_LOCK:`` so the read-filter-write trio on
    ``ACTIVE_TOKENS[username]`` stays atomic; making this function take
    the lock itself would deadlock against those callers because
    ``threading.Lock`` is non-reentrant.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    _prune_revoked_jti(now)
    sessions = ACTIVE_TOKENS.get(username, [])
    active = [(jti, exp) for jti, exp in sessions if exp > now and jti not in REVOKED_JTI]
    ACTIVE_TOKENS[username] = active
    return active


def _jwt_encode(payload: Dict[str, Any], expires_in: int) -> str:
    now = datetime.now(timezone.utc)
    data = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(data, API_SECRET_KEY, algorithm="HS256")


def _jwt_decode(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, API_SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    with _AUTH_LOCK:
        now = int(datetime.now(timezone.utc).timestamp())
        _prune_revoked_jti(now)
        revoked = REVOKED_JTI.get(str(payload.get("jti", "")), 0) > now
    if revoked:
        raise HTTPException(status_code=401, detail="Token revoked")
    return payload


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return auth_header.replace("Bearer ", "", 1).strip()


def _access_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.replace("Bearer ", "", 1).strip()
    cookie_token = request.cookies.get("access_token", "").strip()
    if cookie_token:
        return cookie_token
    raise HTTPException(status_code=401, detail="Missing bearer token")


def _rate_limit(
    scope: str,
    request: Request,
    user: Optional[Dict[str, Any]] = None,
) -> None:
    """Sliding-window rate limit against the in-memory ``RATE_BUCKETS``.

    The body is the classic GET-FILTER-APPEND-PUT cycle on a shared dict,
    so a flood of concurrent requests sharing the same ``key`` would race
    on the filter step: two threads each see ``len(records) == limit - 1``,
    each append their own ``now``, each ``RATE_BUCKETS[key] = records``
    overwriting the peer's write — the net effect is the limit ceiling
    leaks by N×workers and the rate limit becomes advisory. Wrapping the
    read-filter-write in ``_AUTH_LOCK`` is enough to make the ceiling
    accurate within a single process (cross-process scaling still requires
    moving the bucket state into a shared store; see batch E plan).
    """
    if os.getenv("TEST_MODE") == "1":
        return
    path = request.url.path
    if path in METHOD_LIMITS:
        limit, window, strategy = METHOD_LIMITS[path]
    else:
        limit, window, strategy = 120, 60, "ip"
    if strategy == "user" and user:
        key = f"{scope}:{path}:user:{user['sub']}"
    else:
        host = request.client.host if request.client else "unknown"
        key = f"{scope}:{path}:ip:{host}"
    now = time.time()
    with _AUTH_LOCK:
        records = RATE_BUCKETS.get(key, [])
        records = [ts for ts in records if now - ts < window]
        if len(records) >= limit:
            # Keep the filtered bucket persisted so subsequent calls
            # don't re-include the expired entries we just dropped; do
            # NOT append the current ``now`` because the request is
            # being denied.
            RATE_BUCKETS[key] = records
            raise HTTPException(status_code=429, detail="Too many requests")
        records.append(now)
        RATE_BUCKETS[key] = records


def _require_auth(request: Request) -> Dict[str, Any]:
    token = _access_token_from_request(request)
    payload = _jwt_decode(token)
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Access token required")
    _rate_limit("auth", request, payload)
    return payload


def _require_auth_or_token(request: Request) -> Dict[str, Any]:
    return _require_auth(request)


def require_role(role: str):
    def _dep(request: Request) -> Dict[str, Any]:
        payload = _require_auth(request)
        if role == "admin" and payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return payload

    return _dep


def _verify_csrf(request: Request) -> None:
    if request.method in {"POST", "PUT", "DELETE"} and request.url.path != "/api/auth/login":
        header_token = request.headers.get("X-CSRF-Token", "")
        cookie_token = request.cookies.get("csrf_token", "")
        # ``hmac.compare_digest`` runs in time independent of the index of the
        # first differing byte, so an attacker who can measure the response
        # latency cannot probe out the cookie value one byte at a time. The
        # short-circuit on empty strings still leaks "token present" vs "token
        # absent", which is unavoidable when one side is genuinely missing.
        if not header_token or not cookie_token:
            raise HTTPException(status_code=403, detail="CSRF token invalid")
        if not hmac.compare_digest(header_token, cookie_token):
            raise HTTPException(status_code=403, detail="CSRF token invalid")


__all__ = [
    "ACCESS_TOKEN_EXPIRE_SECONDS",
    "ACTIVE_TOKENS",
    "API_SECRET_KEY",
    "MAX_SESSIONS_PER_USER",
    "PASSWORD_CTX",
    "READONLY_USERNAME",
    "REFRESH_TOKEN_EXPIRE_SECONDS",
    "REVOKED_JTI",
    "USERS",
    "_AUTH_LOCK",
    "_access_token_from_request",
    "_bearer_token",
    "_jwt_decode",
    "_jwt_encode",
    "_prune_revoked_jti",
    "_prune_sessions",
    "_rate_limit",
    "_require_auth",
    "_require_auth_or_token",
    "_verify_csrf",
    "require_role",
]
