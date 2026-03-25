"""Shared qBittorrent connection configuration helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

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
    return normalized


def _normalize_qb_url(url: Any, *, allow_insecure_http: Any = _UNSET) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("QB_URL must not be empty.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("QB_URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ValueError("QB_URL must include a host.")
    normalized_path = parsed.path.rstrip("/")
    normalized = SplitResult(
        scheme=parsed.scheme,
        netloc=parsed.netloc,
        path=normalized_path,
        query="",
        fragment="",
    )
    return urlunsplit(normalized)


def build_qb_base_url(
    host: Any = _UNSET,
    port: Any = _UNSET,
    *,
    scheme: Any = _UNSET,
    allow_insecure_http: Any = _UNSET,
) -> str:
    if host is _UNSET:
        qb_url = cfg("QB_URL", None)
        if qb_url:
            return _normalize_qb_url(
                qb_url,
                allow_insecure_http=allow_insecure_http,
            )
        host = cfg("QB_HOST", "your_qbittorrent_ip")
        port = cfg("QB_PORT", "your_qbittorrent_port")
        if scheme is _UNSET:
            scheme = cfg("QB_SCHEME", "https")
    if port is not _UNSET:
        resolved_scheme = qb_scheme(
            scheme,
            allow_insecure_http=allow_insecure_http,
        )
        return f"{resolved_scheme}://{str(host).strip()}:{str(port).strip()}"
    return _normalize_qb_url(
        host,
        allow_insecure_http=allow_insecure_http,
    )


def qb_base_url_candidates(
    host: Any = _UNSET,
    port: Any = _UNSET,
    *,
    scheme: Any = _UNSET,
    allow_insecure_http: Any = _UNSET,
) -> list[str]:
    primary = build_qb_base_url(
        host,
        port,
        scheme=scheme,
        allow_insecure_http=allow_insecure_http,
    )
    candidates = [primary]
    parsed = urlsplit(primary)
    if parsed.scheme == "https":
        fallback = urlunsplit(
            SplitResult(
                scheme="http",
                netloc=parsed.netloc,
                path=parsed.path,
                query="",
                fragment="",
            )
        )
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def masked_qb_base_url(
    host: Any = _UNSET,
    port: Any = _UNSET,
    *,
    scheme: Any = _UNSET,
    allow_insecure_http: Any = _UNSET,
) -> str:
    return mask_ip_address(
        build_qb_base_url(
            host,
            port,
            scheme=scheme,
            allow_insecure_http=allow_insecure_http,
        )
    )


__all__ = [
    "build_qb_base_url",
    "qb_base_url_candidates",
    "masked_qb_base_url",
    "qb_allow_insecure_http",
    "qb_scheme",
    "qb_verify_tls",
]
