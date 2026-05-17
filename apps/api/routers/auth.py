"""Auth and session routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from apps.api.infra.auth import _require_auth
from apps.api.schemas.payloads import (
    ChangePasswordPayload,
    LoginPayload,
    LoginResponse,
    RefreshTokenResponse,
    StatusOkResponse,
)
from apps.api.services import auth_service

router = APIRouter(prefix="/api/auth")


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginPayload, request: Request, response: Response):
    return await auth_service.login_payload(payload, request, response)


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(request: Request, response: Response):
    return await auth_service.refresh_token_payload(request, response)


@router.post("/logout", response_model=StatusOkResponse)
async def logout(response: Response, current=Depends(_require_auth)):
    return await auth_service.logout_payload(response, current)


@router.post("/change-password", response_model=StatusOkResponse)
async def change_password(
    payload: ChangePasswordPayload,
    request: Request,
    current=Depends(_require_auth),
):
    return await auth_service.change_password_payload(payload, request, current)


__all__ = ["change_password", "login", "logout", "refresh_token", "router"]
