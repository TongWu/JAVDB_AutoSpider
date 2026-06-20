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
