"""ADR-046 Phase 1: HistoryRepo resolves session explicitly (arg > bound >
raise) and the two bulk-write methods never read the process-global."""
import inspect

import pytest

from javdb.storage.repos.history_repo import HistoryRepo

_SID = "20260602T000000.000000Z-aaaa-0000"


def test_require_session_resolution_order():
    assert HistoryRepo(session_id=_SID)._require_session() == _SID
    # explicit arg wins over the bound session
    assert HistoryRepo(session_id=_SID)._require_session("OTHER") == "OTHER"
    # explicit arg works with no bound session
    assert HistoryRepo()._require_session("ONLY") == "ONLY"


def test_require_session_raises_when_unbound():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo()._require_session()


def test_batch_update_last_visited_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo().batch_update_last_visited(["https://javdb.com/v/ABC"])


def test_batch_update_movie_actors_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        HistoryRepo().batch_update_movie_actors(
            [("https://javdb.com/v/ABC", "Actor", "female", "/actors/x", "")]
        )


def test_bulk_writes_no_longer_read_the_global():
    """Regression guard for the ambient-session footgun (ADR-046 D2)."""
    src = inspect.getsource(HistoryRepo.batch_update_last_visited)
    src += inspect.getsource(HistoryRepo.batch_update_movie_actors)
    assert "get_active_session_id" not in src


def test_reads_do_not_require_a_session():
    # A session-less repo must still read.
    assert isinstance(HistoryRepo().load_history(), dict)
