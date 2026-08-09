"""ADR-037 Phase 2: a failed run rolls back its staged pending writes."""

from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.db._db_rollback import db_rollback_session
from javdb.storage.sessions.lifecycle import get_state

from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario
from tests.harness.scenarios.golden_daily import golden_daily


def _pending_movie_rows(session_id: str) -> int:
    with get_db(_db.REPORTS_DB_PATH) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM PendingMovieHistoryWrites WHERE SessionId = ?",
            (session_id,),
        ).fetchone()[0]


def test_failure_rolls_back_pending(pipeline_harness):
    base = golden_daily()
    scenario = PipelineScenario(pages=base.pages, qb=FakeQBConfig(fail_adds=True))
    result = pipeline_harness.run_daily(scenario)

    # Uploader failed -> commit was gated off; the session is left for cleanup.
    assert result.uploader_result.exit_code != 0
    assert result.commit_result is None
    session_id = result.spider_result.session_id

    # The spider staged pending movie rows before the uploader failed.
    assert _pending_movie_rows(session_id) > 0

    # Drive the production rollback the cleanup-on-failure job uses.
    db_rollback_session(session_id, dry_run=False)

    # After a full rollback, _rollback_reports DELETEs the ReportSessions row
    # (see the NOTE in _db_rollback.py:_rollback_reports — the row is not left
    # with Status='failed'; it is removed entirely).  get_state therefore
    # returns (None, None) for a successfully rolled-back session.
    assert get_state(session_id).status is None
    assert _pending_movie_rows(session_id) == 0
    assert pipeline_harness.history().count() == 0
