"""Parse + validate the MEDIA_SERVERS config list (ADR-033 D-P3-1).

MEDIA_SERVERS is a PROXY_POOL-style list of dicts in config.py (already
encrypted at rest). Tokens stay inline; they are masked in every repr/log via
javdb.infra.masking.mask_full."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from javdb.infra.masking import mask_full

_SUPPORTED_TYPES = frozenset({"emby", "plex"})
_REQUIRED = ("type", "instance", "base_url", "token")


@dataclass(frozen=True)
class MediaServerConfig:
    source_type: str
    instance: str
    base_url: str
    token: str = field(repr=False)        # never auto-repr the raw token
    libraries: Tuple[str, ...] = ()

    def __repr__(self) -> str:  # masked token for safe logging
        return (
            f"MediaServerConfig(source_type={self.source_type!r}, "
            f"instance={self.instance!r}, base_url={self.base_url!r}, "
            f"token={mask_full(self.token)!r}, libraries={self.libraries!r})"
        )


def parse_media_servers(raw: object) -> list[MediaServerConfig]:
    """Validate the MEDIA_SERVERS list into typed configs. Raises ValueError on
    a malformed entry (fail-fast on config, not at runtime)."""
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("MEDIA_SERVERS must be a list of dicts")

    configs: list[MediaServerConfig] = []
    seen_instances: set[str] = set()
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"MEDIA_SERVERS[{idx}] must be a dict")
        for key in _REQUIRED:
            if not str(entry.get(key, "")).strip():
                raise ValueError(f"MEDIA_SERVERS[{idx}] missing required field: {key}")
        source_type = str(entry["type"]).strip().lower()
        if source_type not in _SUPPORTED_TYPES:
            raise ValueError(
                f"MEDIA_SERVERS[{idx}] unsupported media server type: {entry['type']!r} "
                f"(supported: {sorted(_SUPPORTED_TYPES)})"
            )
        instance = str(entry["instance"]).strip()
        if instance in seen_instances:
            raise ValueError(f"MEDIA_SERVERS[{idx}] duplicate instance: {instance!r}")
        seen_instances.add(instance)
        raw_libs = entry.get("libraries")
        if raw_libs is not None and not isinstance(raw_libs, (list, tuple)):
            raise ValueError(
                f"MEDIA_SERVERS[{idx}] 'libraries' must be a list of names"
            )
        libraries = tuple(
            str(lib).strip() for lib in (raw_libs or ()) if str(lib).strip()
        )
        configs.append(MediaServerConfig(
            source_type=source_type,
            instance=instance,
            base_url=str(entry["base_url"]).strip().rstrip("/"),
            token=str(entry["token"]),
            libraries=libraries,
        ))
    return configs
