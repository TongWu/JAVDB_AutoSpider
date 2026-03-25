"""Auth dependency and token helpers."""

from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Request
from passlib.context import CryptContext

from apps.api.services import context

PASSWORD_CTX = CryptContext(schemes=["bcrypt"], deprecated="auto")

_RUNTIME_ENV = os.getenv("ENVIRONMENT", os.getenv("FLASK_ENV", "")).strip().lower()
_IS_PRODUCTION_ENV = _RUNTIME_ENV == "production"

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
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

ACCESS_TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", "1800"))
REFRESH_TOKEN_EXPIRE_SECONDS = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", str(7 * 24 * 3600))
)
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "3"))

ACTIVE_TOKENS: Dict[str, list[tuple[str, int]]] = {}
REVOKED_JTI: Dict[str, int] = {}
RATE_BUCKETS: Dict[str, list[float]] = {}
_AUTH_LOCK = threading.Lock()

METHOD_LIMITS = {
    "/api/auth/login": (5, 60, "ip"),
    "/api/tasks/daily": (10, 60, "user"),
    "/api/tasks/adhoc": (10, 60, "user"),
    "/api/config": (20, 60, "user"),
    "/api/config/meta": (60, 60, "user"),
}


def _hash_password(plain: str) -> str:
    return PASSWORD_CTX.hash(plain)


ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
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

READONLY_USERNAME = os.getenv("READONLY_USERNAME", "readonly")
READONLY_PASSWORD_HASH = os.getenv("READONLY_PASSWORD_HASH")
if not READONLY_PASSWORD_HASH:
    readonly_password = os.getenv("READONLY_PASSWORD", "").strip()
    if readonly_password:
        context.logger.warning(
            "READONLY_PASSWORD is provided in plaintext env and will be hashed at startup."
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
    ts = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    expired = [jti for jti, exp in REVOKED_JTI.items() if exp <= ts]
    for jti in expired:
        REVOKED_JTI.pop(jti, None)


def _prune_sessions(username: str) -> list[tuple[str, int]]:
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
    records = RATE_BUCKETS.get(key, [])
    records = [ts for ts in records if now - ts < window]
    if not records:
        RATE_BUCKETS.pop(key, None)
    if len(records) >= limit:
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
        if not header_token or not cookie_token or header_token != cookie_token:
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
