"""ADR-024 Phase 1 - file-list feature extraction (pure, no I/O).

Turns a qBittorrent file list (``[{"name", "size", "priority", ...}, ...]``)
into objective evidence features. D10 forbids video-content inspection, so this
uses file names, extensions, and sizes only. The ``PROBE_SCHEMA_VERSION``
constant versions the feature shape so stored evidence stays interpretable as
extraction logic evolves.

``priority`` / ``progress`` are intentionally ignored: evidence describes the
torrent **as published** (all files), not what is currently selected on disk.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

PROBE_SCHEMA_VERSION = "adr024-probe-v1"

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".wmv",
        ".mov",
        ".flv",
        ".ts",
        ".m2ts",
        ".mpg",
        ".mpeg",
        ".m4v",
        ".rmvb",
        ".rm",
        ".vob",
        ".webm",
        ".iso",
    }
)
SUBTITLE_EXTENSIONS = frozenset(
    {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".smi"}
)
# Non-primary content that inflates total torrent size.
JUNK_EXTENSIONS = frozenset(
    {
        ".txt",
        ".nfo",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".url",
        ".html",
        ".htm",
        ".lnk",
        ".db",
        ".exe",
        ".zip",
        ".rar",
        ".7z",
        ".torrent",
    }
)
# Advertising / sample markers in file names (case-insensitive).
JUNK_NAME_MARKERS = (
    "sample",
    "样片",
    "预览",
    "广告",
    "最新地址",
    "更多",
    "扫码",
    "防屏蔽",
    "发布组",
    "宣传",
    "trailer",
)


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _basename(name: str) -> str:
    return os.path.basename(name)


def _is_junk(name: str, ext: str) -> bool:
    if ext in JUNK_EXTENSIONS:
        return True
    lowered = _basename(name).lower()
    return any(marker in lowered for marker in JUNK_NAME_MARKERS)


def extract_file_features(files: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute objective evidence features from a qB file list.

    Returns a dict with the numeric columns expected by
    ``EvidenceRecord`` plus a ``main_video_name`` hint for scoring.
    """
    total = 0
    main_video_size = 0
    main_video_name = ""
    video_count = 0
    subtitle_count = 0
    non_video_count = 0
    junk_bytes = 0
    suspicious = 0

    for f in files:
        name = str(f.get("name", ""))
        try:
            size = int(f.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        total += size
        ext = _ext(name)

        if ext in VIDEO_EXTENSIONS:
            video_count += 1
            if size > main_video_size:
                main_video_size = size
                main_video_name = _basename(name)
        else:
            non_video_count += 1
            if ext in SUBTITLE_EXTENSIONS:
                subtitle_count += 1

        if _is_junk(name, ext):
            junk_bytes += size
            suspicious += 1

    main_video_ratio = (main_video_size / total) if total > 0 else 0.0
    junk_size_ratio = (junk_bytes / total) if total > 0 else 0.0

    return {
        "total_size_bytes": total,
        "main_video_size_bytes": main_video_size,
        "main_video_ratio": main_video_ratio,
        "main_video_name": main_video_name,
        "video_file_count": video_count,
        "subtitle_file_count": subtitle_count,
        "non_video_file_count": non_video_count,
        "junk_size_bytes": junk_bytes,
        "junk_size_ratio": junk_size_ratio,
        "suspicious_file_count": suspicious,
    }
