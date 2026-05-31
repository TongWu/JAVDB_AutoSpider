"""ADR-024 Phase 1 - explainable shadow scoring (pure, no I/O).

Turns the objective features from ``features.extract_file_features`` plus movie
context into a decomposed score and structured reason codes. Per ADR-024 D9 the
score is never opaque: every adjustment maps to a reason code. ``SCORING_VERSION``
versions the rule set so stored evaluations remain interpretable. Phase 1 output
is shadow-only - it never changes the production download decision.
"""

from __future__ import annotations

import re
from typing import Any

SCORING_VERSION = "adr024-shadow-v1"

# Tunable thresholds (kept here, version-stamped via SCORING_VERSION).
JUNK_RATIO_HIGH = 0.15
MAIN_VIDEO_RATIO_FLOOR = 0.5
ACCEPT_SCORE = 0.6
REJECT_SCORE = 0.4

# javdb_category values that claim embedded/sidecar subtitles.
_SUBTITLE_CATEGORIES = frozenset({"subtitle", "hacked_subtitle"})
_NO_SUBTITLE_CATEGORIES = frozenset({"no_subtitle", "hacked_no_subtitle"})
_CJK_SUBTITLE_NAME_MARKERS = ("字幕", "中文", "中字")
_ASCII_SUBTITLE_TOKENS = frozenset(
    {"sub", "subs", "subbed", "chinese", "chs", "cht", "zh", "cn"}
)
_ASCII_FINAL_SUBTITLE_TOKENS = frozenset({"c", "uc", "cu"})
_RESOLUTION_PATTERN = re.compile(r"(?<![a-z0-9])(2160p|4k|1080p|720p)(?![a-z0-9])")


def _subtitle_name_hint(magnet_name: str) -> bool:
    lowered = (magnet_name or "").lower()
    if any(marker in lowered for marker in _CJK_SUBTITLE_NAME_MARKERS):
        return True

    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if any(token in _ASCII_SUBTITLE_TOKENS for token in tokens):
        return True
    return bool(tokens and tokens[-1] in _ASCII_FINAL_SUBTITLE_TOKENS)


def _normalize_resolution_marker(marker: str) -> str:
    return "2160p" if marker == "4k" else marker


def _extract_resolution_marker(text: str) -> str | None:
    lowered = (text or "").lower()
    match = _RESOLUTION_PATTERN.search(lowered)
    if match is None:
        return None
    return _normalize_resolution_marker(match.group(1))


def _main_video_signal(features: dict[str, Any]) -> tuple[float, list[str]]:
    score_delta = 0.0
    reasons: list[str] = []
    if features.get("video_file_count", 0) > 0:
        reasons.append("main_video_detected")
    else:
        reasons.append("main_video_missing")
        score_delta -= 0.7

    main_ratio = float(features.get("main_video_ratio", 0.0))
    if (
        features.get("video_file_count", 0) == 1
        and main_ratio < MAIN_VIDEO_RATIO_FLOOR
    ):
        reasons.append("main_video_ratio_low")
        score_delta -= 0.25
    return score_delta, reasons


def _junk_signal(features: dict[str, Any]) -> tuple[float, list[str]]:
    junk_ratio = float(features.get("junk_size_ratio", 0.0))
    if junk_ratio >= JUNK_RATIO_HIGH:
        return -min(0.4, junk_ratio), ["junk_ratio_high"]
    return 0.0, []


def _subtitle_signal(
    features: dict[str, Any], magnet_name: str
) -> tuple[str, tuple[float, list[str]]]:
    if features.get("subtitle_file_count", 0) > 0:
        return "file_present", (0.0, ["subtitle_file_present"])
    if _subtitle_name_hint(magnet_name):
        return "name_hint", (0.0, ["subtitle_name_hint"])
    return "absent", (0.0, [])


def _category_signal(
    javdb_category: str, subtitle_evidence: str
) -> tuple[bool, tuple[float, list[str]]]:
    if javdb_category in _NO_SUBTITLE_CATEGORIES and subtitle_evidence != "absent":
        return False, (-0.3, ["category_mismatch"])
    if javdb_category in _SUBTITLE_CATEGORIES and subtitle_evidence == "absent":
        return False, (-0.3, ["subtitle_file_missing", "category_mismatch"])
    return True, (0.0, [])


def _resolution_signal(
    features: dict[str, Any], magnet_name: str
) -> tuple[bool | None, tuple[float, list[str]]]:
    claimed_resolution = _extract_resolution_marker(magnet_name)
    main_video_name = str(features.get("main_video_name") or "")
    if not (claimed_resolution and main_video_name):
        return None, (0.0, [])

    main_resolution = _extract_resolution_marker(main_video_name)
    if main_resolution is None:
        return None, (0.0, [])
    if main_resolution == claimed_resolution:
        return True, (0.0, ["resolution_claim_supported"])
    return False, (-0.1, ["resolution_claim_unsupported"])


def _abnormal_file_count_signal(features: dict[str, Any]) -> tuple[float, list[str]]:
    if features.get("suspicious_file_count", 0) >= 5:
        return -0.1, ["abnormal_file_count"]
    return 0.0, []


def _decide(score: float, *, category_consistent: bool) -> str:
    if not category_consistent:
        return "needs_review"
    if score >= ACCEPT_SCORE:
        return "accepted_shadow"
    if score < REJECT_SCORE:
        return "rejected_shadow"
    return "needs_review"


def _apply_signal(
    score: float, reasons: list[str], signal: tuple[float, list[str]]
) -> float:
    """Append signal reasons into the caller-local accumulator and return score."""
    score_delta, signal_reasons = signal
    reasons.extend(signal_reasons)
    return score + score_delta


def score_torrent(
    features: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Return a decomposed shadow score + reason codes + decision.

    ``features`` is the output of ``extract_file_features``. ``context`` carries
    movie-level claims: ``javdb_category``, ``magnet_name``, ``javdb_tags``.
    """
    reasons: list[str] = []
    score = 1.0

    javdb_category = context.get("javdb_category") or ""
    magnet_name = context.get("magnet_name") or ""

    score = _apply_signal(score, reasons, _main_video_signal(features))
    score = _apply_signal(score, reasons, _junk_signal(features))

    subtitle_evidence, subtitle_signal = _subtitle_signal(features, magnet_name)
    score = _apply_signal(score, reasons, subtitle_signal)

    category_consistent, category_signal = _category_signal(
        javdb_category, subtitle_evidence
    )
    score = _apply_signal(score, reasons, category_signal)

    resolution_consistent, resolution_signal = _resolution_signal(
        features, magnet_name
    )
    score = _apply_signal(score, reasons, resolution_signal)

    score = _apply_signal(score, reasons, _abnormal_file_count_signal(features))
    score = max(0.0, min(1.0, score))

    return {
        "score": score,
        "reasons": reasons,
        "subtitle_evidence": subtitle_evidence,
        "category_consistent": category_consistent,
        "resolution_consistent": resolution_consistent,
        "inferred_category": _infer_category(context, subtitle_evidence),
        "decision": _decide(score, category_consistent=category_consistent),
    }


def _infer_category(
    context: dict[str, Any], subtitle_evidence: str
) -> str:
    """Best-effort inferred category from file-list + name hints.

    Phase 1 only distinguishes subtitle vs no_subtitle from file evidence; the
    hacked/censored axis needs content inspection (deferred by D10), so it is
    carried over from the JavDB-claimed category prefix.
    """
    claimed = context.get("javdb_category") or ""
    hacked = claimed.startswith("hacked_")
    has_subtitle = subtitle_evidence in {"file_present", "name_hint"}
    if hacked:
        return "hacked_subtitle" if has_subtitle else "hacked_no_subtitle"
    return "subtitle" if has_subtitle else "no_subtitle"
