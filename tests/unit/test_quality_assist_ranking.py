"""ADR-024 IMP-08: per-category candidate ranking."""

from __future__ import annotations

from javdb.quality.assist import rank_candidates

PROD = "production_download"
PROBE = "quality_probe"


def _c(info_hash, role, score):
    return {"info_hash": info_hash, "target_role": role, "score": score}


def test_ranks_by_score_desc_and_flags_replacement_when_probe_wins():
    ranked = rank_candidates([
        _c("prod", PROD, 0.55),
        _c("probeA", PROBE, 0.80),
        _c("probeB", PROBE, 0.40),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["probeA"]["shadow_rank"] == 1
    assert by_hash["prod"]["shadow_rank"] == 2
    assert by_hash["probeB"]["shadow_rank"] == 3
    assert by_hash["prod"]["would_replace_current_choice"] is True
    assert by_hash["probeA"]["would_replace_current_choice"] is False


def test_no_replacement_when_production_is_best():
    ranked = rank_candidates([
        _c("prod", PROD, 0.90),
        _c("probeA", PROBE, 0.50),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["prod"]["shadow_rank"] == 1
    assert by_hash["prod"]["would_replace_current_choice"] is False


def test_ties_keep_production_ahead_for_stability():
    ranked = rank_candidates([
        _c("probeA", PROBE, 0.60),
        _c("prod", PROD, 0.60),
    ])
    by_hash = {r["info_hash"]: r for r in ranked}
    assert by_hash["prod"]["shadow_rank"] == 1
    assert by_hash["prod"]["would_replace_current_choice"] is False


def test_single_production_candidate():
    ranked = rank_candidates([_c("prod", PROD, 0.7)])
    assert ranked[0]["shadow_rank"] == 1
    assert ranked[0]["would_replace_current_choice"] is False


def test_no_production_candidate_sets_replacement_false_everywhere():
    ranked = rank_candidates([_c("probeA", PROBE, 0.7), _c("probeB", PROBE, 0.5)])
    assert all(r["would_replace_current_choice"] is False for r in ranked)
    assert sorted(r["shadow_rank"] for r in ranked) == [1, 2]


def test_duplicate_info_hash_keeps_distinct_ranks():
    # Two candidates sharing an info_hash must each get a distinct rank — a
    # hash-keyed rank map would collapse them.
    ranked = rank_candidates([
        _c("same", PROD, 0.90),
        _c("same", PROBE, 0.80),
    ])
    assert [r["shadow_rank"] for r in ranked] == [1, 2]
    # production scored highest -> not replaced; the probe row is never "current"
    assert ranked[0]["would_replace_current_choice"] is False
    assert ranked[1]["would_replace_current_choice"] is False
