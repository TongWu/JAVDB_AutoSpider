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
_CJK_SUBTITLE_NAME_MARKERS = ("字幕", "中文", "中字")
_ASCII_SUBTITLE_TOKENS = frozenset(
    {"sub", "subs", "subbed", "chinese", "chs", "cht", "zh", "cn"}
)
_ASCII_FINAL_SUBTITLE_TOKENS = frozenset({"c", "uc", "cu"})


def _subtitle_name_hint(magnet_name: str) -> bool:
    lowered = (magnet_name or "").lower()
    if any(marker in lowered for marker in _CJK_SUBTITLE_NAME_MARKERS):
        return True

    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if any(token in _ASCII_SUBTITLE_TOKENS for token in tokens):
        return True
    return bool(tokens and tokens[-1] in _ASCII_FINAL_SUBTITLE_TOKENS)


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

    # --- main video presence ---
    if features.get("video_file_count", 0) > 0:
        reasons.append("main_video_detected")
    else:
        reasons.append("main_video_missing")
        score -= 0.7

    # --- effective main-video size, not raw total ---
    main_ratio = float(features.get("main_video_ratio", 0.0))
    if features.get("video_file_count", 0) > 0 and main_ratio < MAIN_VIDEO_RATIO_FLOOR:
        reasons.append("main_video_ratio_low")
        score -= 0.25

    # --- junk / ad penalty ---
    junk_ratio = float(features.get("junk_size_ratio", 0.0))
    if junk_ratio >= JUNK_RATIO_HIGH:
        reasons.append("junk_ratio_high")
        score -= min(0.4, junk_ratio)

    # --- subtitle evidence ---
    if features.get("subtitle_file_count", 0) > 0:
        subtitle_evidence = "file_present"
        reasons.append("subtitle_file_present")
    elif _subtitle_name_hint(magnet_name):
        subtitle_evidence = "name_hint"  # weak signal per ADR Scoring Signals
        reasons.append("subtitle_name_hint")
    else:
        subtitle_evidence = "absent"

    # --- category consistency ---
    category_consistent = True
    if javdb_category in _SUBTITLE_CATEGORIES and subtitle_evidence == "absent":
        category_consistent = False
        reasons.append("subtitle_file_missing")
        reasons.append("category_mismatch")
        score -= 0.3

    # --- abnormal file count ---
    if features.get("suspicious_file_count", 0) >= 5:
        reasons.append("abnormal_file_count")
        score -= 0.1

    score = max(0.0, min(1.0, score))

    if not category_consistent:
        decision = "needs_review"
    elif score >= ACCEPT_SCORE:
        decision = "accepted_shadow"
    elif score < REJECT_SCORE:
        decision = "rejected_shadow"
    else:
        decision = "needs_review"

    return {
        "score": score,
        "reasons": reasons,
        "subtitle_evidence": subtitle_evidence,
        "category_consistent": category_consistent,
        "inferred_category": _infer_category(features, context, subtitle_evidence),
        "resolution_consistent": None,  # deferred: deep resolution check (D10-adjacent)
        "decision": decision,
    }


def _infer_category(
    features: dict[str, Any], context: dict[str, Any], subtitle_evidence: str
) -> str:
    """Best-effort inferred category from file-list + name hints.

    Phase 1 only distinguishes subtitle vs no_subtitle from file evidence; the
    hacked/censored axis needs content inspection (deferred by D10), so it is
    carried over from the JavDB-claimed category prefix.
    """
    claimed = context.get("javdb_category") or ""
    hacked = claimed.startswith("hacked_")
    has_subtitle = subtitle_evidence == "file_present"
    if hacked:
        return "hacked_subtitle" if has_subtitle else "hacked_no_subtitle"
    return "subtitle" if has_subtitle else "no_subtitle"
