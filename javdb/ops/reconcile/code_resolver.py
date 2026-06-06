"""Best-effort join-key resolution for media items (ADR-033 D9 / D-P3-5).

Resolves a video_code from a media item's file_path / folder_name / title via a
confidence ladder. Validity of any candidate token is delegated to
javdb.parsing.common so the resolver never re-invents (or drifts from) the
canonical video-code shapes. Codes are normalized with the same
NFKC + strip + upper idiom as the dedup checker (dedup._normalise_code).

Confidence ladder (ADR-033 D9):
  high   — code found in the file_path basename
  medium — code found in folder_name, OR a family-recognized token in title
  low    — only a plausible-but-non-family token found in title
  none   — nothing plausible → caller routes to UnresolvedMediaItem
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

from javdb.ops.reconcile.models import MediaItem
from javdb.parsing.common import (
    _is_plausible_video_code,
    classify_video_code_family,
)

# A delimited candidate token: letters/digits with -/_/. separators, the shape a
# clean code lives in (ABC-123, FC2-PPV-123456, 062216-179, n0656, and dotted
# western-studio-date codes like Wifey.2026.05.30). The loose fallback splits
# free text into the same token alphabet.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Trailing media-filename suffixes that are NOT part of the code: subtitle/uncen
# markers (-C, -U), disc/part markers (-CD1, -DISC2, -PART1), and quality tags
# (-1080P, -2160P, -4K, -8K, -UNCEN[SORED], -LEAK[ED]). Used to recover the base
# code from tokens like 'SSNI-001-C' or 'SSNI-001-1080P' (PR #179 Codex review).
_SUFFIX_RE = re.compile(
    r"-(?:C|U|CD\d+|DISC\d+|PART\d+|\d{3,4}P|2160P|4K|8K|UNCEN(?:SORED)?|LEAK(?:ED)?)$"
)


def _normalise_code(raw: str) -> str:
    """NFKC + strip + upper — identical to dedup._normalise_code (dedup.py:18-30)."""
    return unicodedata.normalize("NFKC", raw or "").strip().upper()


def _canonical(token: str) -> str:
    """Prefer the suffix-stripped base when it is itself a recognized code family.

    'SSNI-001-C' / 'SSNI-001-1080P' -> 'SSNI-001' (classic family), but a genuine
    multi-hyphen code like 'FC2-PPV-123456' is left intact (no known suffix to
    strip, and its base isn't a stronger family)."""
    cur, prev = token, None
    while cur != prev:
        prev, cur = cur, _SUFFIX_RE.sub("", cur)
    if cur != token and classify_video_code_family(cur):
        return cur
    return token


def _scan(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Scan *text* (NFKC-normalized) and return (family_token, plausible_token).

    Each candidate token is reduced to its canonical code (suffix-stripped) before
    classification. Within each tier the first occurrence wins — e.g.
    'STARS-789 disc1' resolves to STARS-789 (family token, appears first)."""
    if not text:
        return None, None
    normalized = _normalise_code(text)
    family_hit: Optional[str] = None
    plausible_hit: Optional[str] = None
    for raw_token in _TOKEN_RE.findall(normalized):
        token = _canonical(_normalise_code(raw_token))
        if classify_video_code_family(token):
            family_hit = family_hit or token
        elif _is_plausible_video_code(token):
            plausible_hit = plausible_hit or token
    return family_hit, plausible_hit


def _first_plausible(text: Optional[str]) -> Optional[str]:
    """Return the first plausible token in *text*, preferring a family hit."""
    family, plausible = _scan(text)
    return family or plausible


def resolve_video_code(item: MediaItem) -> tuple[Optional[str], str]:
    """Resolve (video_code, confidence) for a media item.

    Ladder (ADR-033 D9):
      high   — code found in the file_path basename
      medium — code found in folder_name, OR a family-recognized token in title
      low    — only a plausible-but-non-family token found in title
      none   — nothing plausible → caller routes to UnresolvedMediaItem
    """
    # high: strict scan of the file basename.
    if item.file_path:
        basename = os.path.basename(item.file_path)
        stem = os.path.splitext(basename)[0]
        hit = _first_plausible(stem)
        if hit:
            return hit, "high"

    # medium (folder): folder_name (family or plausible).
    folder_hit = _first_plausible(item.folder_name)
    if folder_hit:
        return folder_hit, "medium"

    # medium/low from title: family-recognized → medium; plausible-only → low.
    family_token, plausible_token = _scan(item.title)
    if family_token:
        return family_token, "medium"
    if plausible_token:
        return plausible_token, "low"

    return None, "none"
