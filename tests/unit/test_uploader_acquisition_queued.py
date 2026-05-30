import os
import sqlite3
import sys
from unittest.mock import MagicMock
from types import ModuleType

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# Create a proper mock config module with actual values
mock_config = ModuleType('config')
mock_config.QB_URL = 'https://localhost:8080'
mock_config.QB_HOST = 'localhost'
mock_config.QB_PORT = '8080'
mock_config.QB_USERNAME = 'admin'
mock_config.QB_PASSWORD = 'adminadmin'
mock_config.TORRENT_CATEGORY = 'JavDB'
mock_config.TORRENT_CATEGORY_ADHOC = 'Ad Hoc'
mock_config.TORRENT_SAVE_PATH = ''
mock_config.AUTO_START = True
mock_config.SKIP_CHECKING = False
mock_config.REQUEST_TIMEOUT = 30
mock_config.DELAY_BETWEEN_ADDITIONS = 1
mock_config.UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
mock_config.REPORTS_DIR = 'reports'
mock_config.DAILY_REPORT_DIR = 'reports/DailyReport'
mock_config.AD_HOC_DIR = 'reports/AdHoc'
mock_config.LOG_LEVEL = 'INFO'
mock_config.PROXY_HTTP = None
mock_config.PROXY_HTTPS = None
mock_config.PROXY_MODULES = ['all']
mock_config.PROXY_MODE = 'single'
mock_config.PROXY_POOL = []
mock_config.PROXY_POOL_MAX_FAILURES = 3
mock_config.GIT_USERNAME = 'test'
mock_config.GIT_PASSWORD = ''
mock_config.GIT_REPO_URL = ''
mock_config.GIT_BRANCH = 'main'
sys.modules['config'] = mock_config

from javdb.integrations.qb.uploader import service as uploader_service
from javdb.ops.reconcile import service as reconcile_service
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


def test_uploader_helper_writes_queued_row():
    """Pin the contract the uploader relies on: a successful add -> one queued row."""
    c = sqlite3.connect(':memory:')
    c.executescript(_DDL)
    repo = AcquisitionOutcomeRepo(c)
    torrent = {
        'magnet': 'magnet:?xt=urn:btih:' + 'b' * 40,
        'title': 'ABC-1 [sub]',
        'type': 'subtitle',
        'href': '/v/ABC-1',
        'video_code': 'ABC-1',
    }

    reconcile_service.record_queued(torrent, session_id='S9', repo=repo)

    got = repo.get('b' * 40)
    assert got is not None
    assert got.state == 'queued'
    assert got.queued_at is not None
    assert got.session_id == 'S9'


def test_uploader_success_helper_delegates_queued_write(monkeypatch):
    calls = []
    monkeypatch.setattr(
        uploader_service,
        '_record_acquisition_queued',
        lambda torrent, session_id: calls.append((torrent, session_id)),
    )

    torrent = {
        'magnet': 'magnet:?xt=urn:btih:' + 'c' * 40,
        'title': 'ABC-2 [sub]',
        'type': 'subtitle',
        'href': '/v/ABC-2',
        'video_code': 'ABC-2',
    }

    uploader_service._record_queued_acquisition(torrent, 'S9')

    assert calls == [(torrent, 'S9')]


def test_run_uploader_success_path_records_queued(monkeypatch):
    from javdb.integrations.qb.uploader.options import QbUploaderOptions

    queued_calls = []
    sink = MagicMock()
    sink.saved = False
    sink.error = None
    sink.backend = 'mock'

    monkeypatch.setattr(uploader_service, 'global_proxy_helper', None)
    monkeypatch.setattr(uploader_service, 'initialize_proxy_helper', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(uploader_service, 'test_qbittorrent_connection', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        uploader_service,
        'resolve_qb_uploader_csv_path',
        lambda **_kwargs: MagicMock(source='manual', path='fake.csv'),
    )
    monkeypatch.setattr(uploader_service, 'read_csv_file', lambda _path: ([{
        'magnet': 'magnet:?xt=urn:btih:' + 'd' * 40,
        'title': 'ABC-3 [sub]',
        'type': 'subtitle',
        'href': '/v/ABC-3',
        'video_code': 'ABC-3',
    }], True))
    monkeypatch.setattr(uploader_service, 'login_to_qbittorrent', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(uploader_service, 'get_existing_torrents', lambda *_args, **_kwargs: set())
    monkeypatch.setattr(uploader_service, 'add_torrent_to_qbittorrent', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(uploader_service, 'time', MagicMock(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        uploader_service,
        'save_uploader_stats',
        lambda *_args, **_kwargs: sink,
    )
    monkeypatch.setattr(uploader_service, 'commit_workflow_outputs', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        uploader_service,
        '_record_acquisition_queued',
        lambda torrent, session_id: queued_calls.append((torrent, session_id)),
    )

    result = uploader_service.run_uploader(QbUploaderOptions(mode='daily', session_id='S9'))

    assert result.exit_code == 0
    assert queued_calls == [({
        'magnet': 'magnet:?xt=urn:btih:' + 'd' * 40,
        'title': 'ABC-3 [sub]',
        'type': 'subtitle',
        'href': '/v/ABC-3',
        'video_code': 'ABC-3',
    }, 'S9')]
