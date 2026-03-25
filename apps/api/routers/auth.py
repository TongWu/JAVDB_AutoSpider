"""Auth and session routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from apps.api.infra.auth import _require_auth
from apps.api.schemas.payloads import LoginPayload
from apps.api.services import auth_service

router = APIRouter(prefix="/api/auth")


@router.post("/login")
async def login(payload: LoginPayload, request: Request, response: Response):
    return await auth_service.login_payload(payload, request, response)


@router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    return await auth_service.refresh_token_payload(request, response)


@router.post("/logout")
async def logout(response: Response, current=Depends(_require_auth)):
    return await auth_service.logout_payload(response, current)


__all__ = ["login", "logout", "refresh_token", "router"]
