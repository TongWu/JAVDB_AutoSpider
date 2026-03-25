"""Shared qBittorrent connection configuration helpers."""

from __future__ import annotations

from typing import Any

from packages.python.javdb_core.masking import mask_ip_address
from packages.python.javdb_platform.config_helper import cfg

_UNSET = object()
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def qb_allow_insecure_http(value: Any = _UNSET) -> bool:
    if value is _UNSET:
        value = cfg("QB_ALLOW_INSECURE_HTTP", False)
    return _coerce_bool(value, default=False)


def qb_verify_tls(value: Any = _UNSET) -> bool:
    if value is _UNSET:
        value = cfg("QB_VERIFY_TLS", True)
    return _coerce_bool(value, default=True)


def qb_scheme(value: Any = _UNSET, *, allow_insecure_http: Any = _UNSET) -> str:
    if value is _UNSET:
        value = cfg("QB_SCHEME", "https")
    normalized = str(value or "https").strip().lower() or "https"
    if normalized not in {"https", "http"}:
        raise ValueError("QB_SCHEME must be either 'https' or 'http'.")
    if normalized == "http" and not qb_allow_insecure_http(allow_insecure_http):
        raise ValueError("QB_SCHEME=http requires QB_ALLOW_INSECURE_HTTP=true.")
    return normalized


def build_qb_base_url(
    host: Any,
    port: Any,
    *,
    scheme: Any = _UNSET,
    allow_insecure_http: Any = _UNSET,
) -> str:
    resolved_scheme = qb_scheme(
        scheme,
        allow_insecure_http=allow_insecure_http,
    )
    return f"{resolved_scheme}://{str(host).strip()}:{str(port).strip()}"


def masked_qb_base_url(
    host: Any,
    port: Any,
    *,
    scheme: Any = _UNSET,
    allow_insecure_http: Any = _UNSET,
) -> str:
    resolved_scheme = qb_scheme(
        scheme,
        allow_insecure_http=allow_insecure_http,
    )
    return f"{resolved_scheme}://{mask_ip_address(str(host).strip())}:{str(port).strip()}"


__all__ = [
    "build_qb_base_url",
    "masked_qb_base_url",
    "qb_allow_insecure_http",
    "qb_scheme",
    "qb_verify_tls",
]
