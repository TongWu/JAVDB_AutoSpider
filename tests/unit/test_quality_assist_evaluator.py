"""ADR-024 IMP-08 Task 4: assist evaluator gated orchestration."""

from __future__ import annotations

import pytest

from javdb.quality.assist_evaluator import evaluate_assist_for_movies


class _Repo:
    def __init__(self, evidence_by_movie):
        self._ev = evidence_by_movie
        self.evaluations = []

    def list_evidence_for_movie(self, movie_href, *, probe_schema_version=None):
        return self._ev.get(movie_href, [])

    def upsert_evaluation(self, rec):
        self.evaluations.append(rec)


def _ev(info_hash, role, *, junk_ratio=0.0, main_ratio=0.95, cat="subtitle",
        name="ABC-123-C", subs=1):
    return {
        "info_hash": info_hash, "target_role": role, "movie_href": "/v/abc",
        "javdb_category": cat, "magnet_name": name,
        "total_size_bytes": 5_000_000_000, "main_video_size_bytes": 4_800_000_000,
        "main_video_ratio": main_ratio, "video_file_count": 1,
        "subtitle_file_count": subs, "non_video_file_count": 0,
        "junk_size_bytes": 0, "junk_size_ratio": junk_ratio,
        "suspicious_file_count": 0, "main_video_name": name + ".mkv",
    }


def test_evaluator_ranks_and_flags_replacement():
    repo = _Repo({"/v/abc": [
        _ev("prod", "production_download", junk_ratio=0.40),   # junk -> low score
        _ev("probeA", "quality_probe", junk_ratio=0.0),         # clean -> high score
    ]})
    summary = evaluate_assist_for_movies(["/v/abc"], repo=repo, policy_mode="assist")
    assert summary["candidates"] == 2
    by_hash = {e.info_hash: e for e in repo.evaluations}
    assert by_hash["prod"].policy_mode == "assist"
    assert by_hash["probeA"].shadow_rank == 1
    assert by_hash["prod"].would_replace_current_choice is True
    assert summary["would_replace"] == 1


def test_duplicate_role_info_hash_writes_one_row_and_keeps_rank_1():
    """Regression: one info_hash can be both the production download and a probe
    runner-up. Both UPSERT onto (info_hash, movie_href, scoring_version), so the
    second write used to clobber the first — dropping rank 1 or the production
    row. The two rows are the same torrent, so exactly one evaluation is written,
    it keeps the production role, and it is not flagged as replacing itself."""
    repo = _Repo({"/v/abc": [
        _ev("shared", "production_download", junk_ratio=0.0),
        _ev("shared", "quality_probe", junk_ratio=0.0),
        _ev("probeB", "quality_probe", junk_ratio=0.40),  # junk -> loses
    ]})
    summary = evaluate_assist_for_movies(["/v/abc"], repo=repo, policy_mode="assist")

    assert summary["candidates"] == 2
    written = [e.info_hash for e in repo.evaluations]
    assert written.count("shared") == 1
    by_hash = {e.info_hash: e for e in repo.evaluations}
    assert by_hash["shared"].shadow_rank == 1
    # The production download is not "replaced" by its own probe copy.
    assert by_hash["shared"].would_replace_current_choice is False
    assert summary["would_replace"] == 0


def test_collapse_prefers_production_regardless_of_input_order():
    """The evaluator test above only exercises the 'first one wins' path, because
    the production row happens to come first. EvaluationRecord carries no
    target_role, so it cannot prove which row survived either. Test the collapse
    directly with the probe row first — that is the branch where production has
    to displace an already-kept probe."""
    from javdb.quality.assist import PRODUCTION_TARGET_ROLE
    from javdb.quality.assist_evaluator import _collapse_duplicate_roles

    probe_first = _collapse_duplicate_roles([
        _ev("shared", "quality_probe"),
        _ev("shared", "production_download"),
    ])
    assert len(probe_first) == 1
    assert probe_first[0]["target_role"] == PRODUCTION_TARGET_ROLE

    production_first = _collapse_duplicate_roles([
        _ev("shared", "production_download"),
        _ev("shared", "quality_probe"),
    ])
    assert len(production_first) == 1
    assert production_first[0]["target_role"] == PRODUCTION_TARGET_ROLE

    # Distinct hashes are never collapsed, whatever their roles.
    distinct = _collapse_duplicate_roles([
        _ev("a", "production_download"),
        _ev("b", "quality_probe"),
    ])
    assert {row["info_hash"] for row in distinct} == {"a", "b"}


def test_evaluator_noop_when_not_assist():
    repo = _Repo({"/v/abc": [_ev("prod", "production_download")]})
    summary = evaluate_assist_for_movies(["/v/abc"], repo=repo, policy_mode="shadow")
    assert summary == {"movies": 0, "candidates": 0, "would_replace": 0}
    assert repo.evaluations == []


def test_run_assist_reads_policy_mode_from_cfg(monkeypatch):
    """Regression: run_assist must resolve policy_mode via cfg() — a getattr on
    the config module silently returns 'shadow' (the mode is not a module attr),
    which would make run_assist a permanent no-op even in assist mode."""
    import contextlib

    import javdb.infra.config as config_mod
    import javdb.quality.assist_evaluator as ae
    import javdb.storage.db as db_mod
    import javdb.storage.repos.torrent_quality_repo as repo_mod

    monkeypatch.setattr(
        config_mod, "cfg",
        lambda key, default=None: "assist" if key == "TORRENT_QUALITY_POLICY_MODE" else default,
    )

    @contextlib.contextmanager
    def _fake_get_db(_path):
        yield object()

    monkeypatch.setattr(db_mod, "get_db", _fake_get_db)

    seen = {}

    class _Repo2:
        def __init__(self, _conn):
            pass

        def list_recent_evaluations(self, *, limit=500, since=None):
            seen["since"] = since
            return [{"movie_href": "/v/abc"}]

    monkeypatch.setattr(repo_mod, "TorrentQualityRepo", _Repo2)

    captured = {}

    def _fake_eval(movie_hrefs, *, repo, policy_mode, **kw):
        captured["policy_mode"] = policy_mode
        return {"movies": 1, "candidates": 0, "would_replace": 0}

    monkeypatch.setattr(ae, "evaluate_assist_for_movies", _fake_eval)

    ae.run_assist(days=7)
    # With the old getattr bug this would be "shadow"; cfg() makes it "assist".
    assert captured["policy_mode"] == "assist"
    # --days is honoured: the since cutoff matches ~7 days ago (day-granularity).
    from datetime import datetime, timedelta, timezone
    expected_day = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    assert seen["since"].startswith(expected_day)


def test_run_assist_rejects_non_positive_days():
    import javdb.quality.assist_evaluator as ae
    with pytest.raises(ValueError):
        ae.run_assist(days=0)
    with pytest.raises(ValueError):
        ae.run_assist(days=-3)
