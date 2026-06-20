"""ADR-024 IMP-08: per-category candidate ranking (pure, no I/O).

Given the scored candidates for ONE (movie_href, category) — the production
download plus any quality_probe runner-ups — assign shadow_rank (1 = best) and
flag would_replace_current_choice on the production candidate iff a probe
candidate outranks it. Shadow-only: this never changes the production download.
"""

from __future__ import annotations

from typing import Any, Dict, List

PRODUCTION_TARGET_ROLE = "production_download"
PROBE_TARGET_ROLE = "quality_probe"


def _sort_key(c: Dict[str, Any]):
    # Score desc; on ties keep the production_download candidate ahead so a tie
    # never "replaces" the current choice (stability). Final tie-break info_hash.
    is_prod = 0 if c.get("target_role") == PRODUCTION_TARGET_ROLE else 1
    return (-float(c.get("score") or 0.0), is_prod, str(c.get("info_hash") or ""))


def rank_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return new dicts (input not mutated) with shadow_rank +
    would_replace_current_choice added; original input order is preserved."""
    # Rank by ORIGINAL POSITION, not info_hash: two candidates can share an
    # info_hash (e.g. the same torrent surfaced as both production and a probe
    # runner-up), and a hash-keyed rank map would collapse their ranks.
    order = sorted(enumerate(candidates), key=lambda pair: _sort_key(pair[1]))
    rank_by_index = {idx: rank + 1 for rank, (idx, _) in enumerate(order)}
    best_idx = order[0][0] if order else None

    out: List[Dict[str, Any]] = []
    for idx, c in enumerate(candidates):
        rank = rank_by_index[idx]
        is_prod = c.get("target_role") == PRODUCTION_TARGET_ROLE
        replace = bool(is_prod and best_idx is not None and best_idx != idx)
        out.append({**c, "shadow_rank": rank, "would_replace_current_choice": replace})
    return out
