"""Auth route service logic."""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import HTTPException, Request, Response

from apps.api.infra import auth as auth_infra
from apps.api.schemas.payloads import LoginPayload
from apps.api.services import context


def _access_claims(token: str) -> dict:
    return jwt.decode(
        token,
        auth_infra.API_SECRET_KEY,
        algorithms=["HS256"],
    )


async def login_payload(
    payload: LoginPayload,
    request: Request,
    response: Response,
) -> dict:
    auth_infra._rate_limit("preauth", request)
    user = auth_infra.USERS.get(payload.username)
    if not user or not auth_infra.PASSWORD_CTX.verify(
        payload.password, user["password_hash"]
    ):
        context.audit_logger.warning(
            "login_failed username=%s ip=%s",
            payload.username,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid username/password")

    access = auth_infra._jwt_encode(
        {"sub": payload.username, "role": user["role"], "typ": "access"},
        auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    refresh = auth_infra._jwt_encode(
        {"sub": payload.username, "role": user["role"], "typ": "refresh"},
        auth_infra.REFRESH_TOKEN_EXPIRE_SECONDS,
    )
    access_claims = _access_claims(access)
    with auth_infra._AUTH_LOCK:
        sessions = auth_infra._prune_sessions(payload.username)
        if len(sessions) >= auth_infra.MAX_SESSIONS_PER_USER:
            raise HTTPException(status_code=403, detail="Too many active sessions")
        sessions.append((access_claims["jti"], access_claims["exp"]))
        auth_infra.ACTIVE_TOKENS[payload.username] = sessions

    csrf = auth_infra.secrets.token_urlsafe(24)
    response.set_cookie(
        "access_token",
        access,
        httponly=True,
        samesite="lax",
        secure=context.COOKIE_SECURE,
        max_age=auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    response.set_cookie(
        "csrf_token",
        csrf,
        httponly=False,
        samesite="lax",
        secure=context.COOKIE_SECURE,
    )
    context.audit_logger.info(
        "login_success username=%s ip=%s role=%s",
        payload.username,
        request.client.host if request.client else "unknown",
        user["role"],
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
        "csrf_token": csrf,
        "role": user["role"],
        "username": payload.username,
    }


async def refresh_token_payload(request: Request, response: Response) -> dict:
    token = auth_infra._bearer_token(request)
    payload = auth_infra._jwt_decode(token)
    if payload.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh token required")
    auth_infra._rate_limit("preauth", request)
    username = payload.get("sub", "")
    user = auth_infra.USERS.get(username)
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")

    access = auth_infra._jwt_encode(
        {"sub": username, "role": user["role"], "typ": "access"},
        auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    access_claims = _access_claims(access)
    with auth_infra._AUTH_LOCK:
        sessions = auth_infra._prune_sessions(username)
        if len(sessions) >= auth_infra.MAX_SESSIONS_PER_USER:
            sessions.pop(0)
        sessions.append((access_claims["jti"], access_claims["exp"]))
        auth_infra.ACTIVE_TOKENS[username] = sessions

    context.audit_logger.info("token_refresh username=%s", username)
    response.set_cookie(
        "access_token",
        access,
        httponly=True,
        samesite="lax",
        secure=context.COOKIE_SECURE,
        max_age=auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": auth_infra.ACCESS_TOKEN_EXPIRE_SECONDS,
    }


async def logout_payload(response: Response, current: dict) -> dict:
    jti = current.get("jti")
    if jti:
        with auth_infra._AUTH_LOCK:
            exp = int(
                current.get("exp", int(datetime.now(timezone.utc).timestamp()))
            )
            auth_infra.REVOKED_JTI[str(jti)] = exp
            auth_infra._prune_revoked_jti()
            sessions = auth_infra.ACTIVE_TOKENS.get(current["sub"], [])
            auth_infra.ACTIVE_TOKENS[current["sub"]] = [
                (session_jti, session_exp)
                for session_jti, session_exp in sessions
                if session_jti != jti
            ]
    context.audit_logger.info("logout username=%s", current["sub"])
    response.delete_cookie("access_token")
    return {"status": "ok"}


__all__ = [
    "login_payload",
    "logout_payload",
    "refresh_token_payload",
]
