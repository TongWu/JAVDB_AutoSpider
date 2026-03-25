"""Shared API guards and validation helpers."""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

_SAFE_OUTPUT_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _build_allowed_hosts() -> frozenset[str]:
    """Derive allowed target hosts from config.BASE_URL + javdb.com defaults."""
    hosts = {"javdb.com", "www.javdb.com"}
    try:
        import config as cfg

        base_url = getattr(cfg, "BASE_URL", "")
        if base_url:
            parsed = urlparse(base_url)
            if parsed.hostname:
                hosts.add(parsed.hostname.lower())
    except ImportError:
        pass
    return frozenset(hosts)


ALLOWED_HOSTS = _build_allowed_hosts()


def _validate_target_url(url: str) -> None:
    """Reject URLs whose scheme/host fall outside the allowlist (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"URL scheme must be http or https, got {parsed.scheme!r}",
        )
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail=f"Host {host!r} is not in the allowed domain list",
        )


def _is_valid_javdb_host(url: str) -> bool:
    """Return True if *url* targets a known JavDB hostname."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in ALLOWED_HOSTS


def _resolve_public_target_or_422(url: str) -> tuple[object, str, str]:
    """Validate *url* and resolve it to a single public IP address."""

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="url must use http or https scheme")
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="url must include a host")
    if not _is_valid_javdb_host(url):
        raise HTTPException(
            status_code=422,
            detail="url must target a valid javdb.com host",
        )

    hostname = parsed.hostname or ""
    try:
        resolved_public_ips: set[str] = set()
        for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            hostname,
            parsed.port,
            proto=socket.IPPROTO_TCP,
        ):
            ip = ipaddress.ip_address(sockaddr[0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                raise HTTPException(
                    status_code=422,
                    detail="url must not resolve to a private or reserved IP address",
                )
            resolved_public_ips.add(str(ip))
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=422,
            detail="url host DNS resolution failed",
        ) from exc

    if not resolved_public_ips:
        raise HTTPException(status_code=422, detail="url host DNS resolution failed")
    if len(resolved_public_ips) > 1:
        raise HTTPException(
            status_code=422,
            detail="url host resolves to multiple public IPs; rejected to prevent DNS rebinding",
        )
    return parsed, hostname, next(iter(resolved_public_ips))


def _sanitize_output_filename(value: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValueError("output_file cannot be empty")
    if "/" in name or "\\" in name:
        raise ValueError("output_file cannot contain path separators")
    if ".." in name:
        raise ValueError("output_file cannot contain parent traversal")
    if Path(name).is_absolute() or re.match(r"^[A-Za-z]:", name):
        raise ValueError("output_file must be a relative filename")
    if not _SAFE_OUTPUT_FILE_RE.fullmatch(name):
        raise ValueError("output_file contains invalid characters")
    return name


__all__ = [
    "ALLOWED_HOSTS",
    "_is_valid_javdb_host",
    "_resolve_public_target_or_422",
    "_sanitize_output_filename",
    "_validate_target_url",
]
