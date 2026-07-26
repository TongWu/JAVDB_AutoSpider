"""Which backend the email's stats come from.

Guards ``javdb.integrations.notify.email.service._load_run_stats``:

* ``sqlite`` / ``dual`` → forced-local reads (the original P0-6 rule). Under
  dual this is an observability exception, not an authority claim: D1 is still
  the source of truth, and reading the side that definitely got the write is
  what makes a D1 shortfall visible instead of silently understating the run.
* ``d1``  → D1 reads. Before this was fixed the helper always took the
  forced-local path, which after the d1-only cutover could only ever miss
  the run's SessionId (nothing writes the SQLite mirror any more) and
  silently degrade every email to CSV/log-derived numbers.
"""

from __future__ import annotations

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from javdb.integrations.notify.email import service  # noqa: E402

SPIDER_D1 = {'TotalProcessed': 11}
SPIDER_LOCAL = {'TotalProcessed': 22}


class _StubStatsRepo:
    """Records which variant was called and returns a distinguishable row."""

    calls: list[str] = []

    def get_spider_stats(self, sid):
        self.calls.append(f'd1:spider:{sid}')
        return SPIDER_D1

    def get_uploader_stats(self, sid):
        self.calls.append(f'd1:uploader:{sid}')
        return {'TotalTorrents': 1}

    def get_pikpak_stats(self, sid):
        self.calls.append(f'd1:pikpak:{sid}')
        return {'TotalTorrents': 2}

    def get_spider_stats_local(self, sid):
        self.calls.append(f'local:spider:{sid}')
        return SPIDER_LOCAL

    def get_uploader_stats_local(self, sid):
        self.calls.append(f'local:uploader:{sid}')
        return {'TotalTorrents': 3}

    def get_pikpak_stats_local(self, sid):
        self.calls.append(f'local:pikpak:{sid}')
        return {'TotalTorrents': 4}


@pytest.fixture
def stub_backend(monkeypatch):
    """Point _load_run_stats at stubs; yields a setter for the backend name."""
    import javdb.infra.config as infra_config
    import javdb.storage.db as storage_db
    import javdb.storage.repos.stats_repo as stats_repo

    calls: list[str] = []
    _StubStatsRepo.calls = calls

    monkeypatch.setattr(infra_config, 'use_sqlite', lambda: True)
    monkeypatch.setattr(storage_db, 'init_db', lambda *a, **k: None)
    monkeypatch.setattr(stats_repo, 'StatsRepo', _StubStatsRepo)

    def _set(backend: str):
        monkeypatch.setattr(storage_db, 'current_backend', lambda: backend)
        return calls

    return _set


@pytest.mark.parametrize('backend', ['sqlite', 'dual'])
def test_sqlite_and_dual_read_the_forced_local_variants(stub_backend, backend):
    calls = stub_backend(backend)

    stats = service._load_run_stats('20260726T000000.000000Z-0001-0001')

    assert stats.spider is SPIDER_LOCAL
    assert all(c.startswith('local:') for c in calls), calls
    assert 'forced sqlite-local' in stats.backend_label
    assert backend in stats.backend_label


def test_d1_backend_reads_d1_not_the_stale_local_mirror(stub_backend):
    calls = stub_backend('d1')

    stats = service._load_run_stats('20260726T000000.000000Z-0001-0001')

    assert stats.spider is SPIDER_D1
    assert all(c.startswith('d1:') for c in calls), calls
    assert stats.backend_label == 'd1 (stats from D1)'


def test_d1_backend_reads_d1_even_when_use_sqlite_is_false(stub_backend, monkeypatch):
    """STORAGE_MODE=csv must not suppress the D1 read.

    The old gate was ``if use_sqlite()``, which keys off STORAGE_MODE — an
    unrelated csv-vs-db axis — so a csv-only deployment lost stats entirely.
    """
    import javdb.infra.config as infra_config

    calls = stub_backend('d1')
    monkeypatch.setattr(infra_config, 'use_sqlite', lambda: False)

    stats = service._load_run_stats('sid-1')

    assert stats.spider is SPIDER_D1
    assert calls == ['d1:spider:sid-1', 'd1:uploader:sid-1', 'd1:pikpak:sid-1']


def test_sqlite_backend_with_use_sqlite_false_loads_nothing(stub_backend, monkeypatch):
    """csv-only + sqlite backend: no DB to read, and no attempt to."""
    import javdb.infra.config as infra_config

    calls = stub_backend('sqlite')
    monkeypatch.setattr(infra_config, 'use_sqlite', lambda: False)

    stats = service._load_run_stats('sid-1')

    assert stats == service._RunStats()
    assert calls == []


def test_missing_session_id_falls_back_per_backend(stub_backend, monkeypatch):
    """The latest-session lookup must use a backend-aware repo under d1.

    The d1 stub mirrors the real ``SessionsRepo.__init__(self, conn)``
    signature on purpose: an earlier revision instantiated it bare, which
    raised TypeError straight into the outer except and silently produced
    empty stats. A no-arg stub would have kept that green.
    """
    import contextlib

    import javdb.storage.db as storage_db
    import javdb.storage.repos.sessions_repo as sessions_repo
    import javdb.storage.repos.session_lifecycle_repo as lifecycle_repo

    sentinel_conn = object()
    seen_conn = []

    class _StubSessionsRepo:
        def __init__(self, conn):          # positional, exactly like the real one
            seen_conn.append(conn)

        def get_latest_session(self, *a, **k):
            return {'Id': 'from-d1'}

    class _StubLifecycleRepo:
        def get_latest_session_local(self, *a, **k):
            return {'Id': 'from-sqlite'}

    @contextlib.contextmanager
    def _fake_get_db(path):
        yield sentinel_conn

    monkeypatch.setattr(storage_db, 'get_db', _fake_get_db)
    monkeypatch.setattr(sessions_repo, 'SessionsRepo', _StubSessionsRepo)
    monkeypatch.setattr(lifecycle_repo, 'SessionLifecycleRepo', _StubLifecycleRepo)

    stub_backend('d1')
    assert service._load_run_stats(None).session_id == 'from-d1'
    assert seen_conn == [sentinel_conn], "SessionsRepo must be built with a connection"

    stub_backend('sqlite')
    assert service._load_run_stats(None).session_id == 'from-sqlite'


def test_repo_errors_degrade_to_empty_stats(stub_backend, monkeypatch):
    """Stats are decoration — a backend outage must not break the email."""
    import javdb.storage.repos.stats_repo as stats_repo

    class _Exploding:
        def get_spider_stats(self, sid):
            raise RuntimeError('D1 unreachable')

        get_uploader_stats = get_spider_stats
        get_pikpak_stats = get_spider_stats

    stub_backend('d1')
    monkeypatch.setattr(stats_repo, 'StatsRepo', _Exploding)

    stats = service._load_run_stats('sid-1')

    assert stats.spider is None
    assert stats.uploader is None
    assert stats.pikpak is None
    # The label survives so the email still reports which backend was tried.
    assert stats.backend_label == 'd1 (stats from D1)'


def test_one_failing_metric_keeps_the_others(stub_backend, monkeypatch):
    """A transient failure on one query must not discard the successful ones.

    These are three independent round trips under d1. Building the result in a
    single expression made any one of them fatal to all three, which is a
    regression against the pre-refactor sequential assignment.
    """
    import javdb.storage.repos.stats_repo as stats_repo

    class _PikpakDown:
        def get_spider_stats(self, sid):
            return SPIDER_D1

        def get_uploader_stats(self, sid):
            return {'TotalTorrents': 1}

        def get_pikpak_stats(self, sid):
            raise RuntimeError('D1 timeout on PikpakStats')

    stub_backend('d1')
    monkeypatch.setattr(stats_repo, 'StatsRepo', _PikpakDown)

    stats = service._load_run_stats('sid-1')

    assert stats.spider is SPIDER_D1, "spider stats were fetched before the failure"
    assert stats.uploader == {'TotalTorrents': 1}
    assert stats.pikpak is None
    assert stats.session_id == 'sid-1'
    assert stats.backend_label == 'd1 (stats from D1)'
