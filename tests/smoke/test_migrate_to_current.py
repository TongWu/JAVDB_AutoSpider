import os
import sys
import types

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import javdb.migrations.migrate_to_current as migrate_to_current


@pytest.fixture(autouse=True)
def _clean_align_bootstrap_env():
    """``migrate_to_current.main()`` calls ``_bootstrap_storage_backend_for_align``
    under ``--align-inventory-history``, which writes STORAGE_BACKEND /
    STRICT_DUAL_WRITE directly into ``os.environ`` (bypassing monkeypatch).
    Without explicit cleanup those leak into every subsequent test in the
    session and break ~250 unrelated tests with
    ``ValueError: No D1 logical-name mapping``.
    """
    yield
    os.environ.pop("STORAGE_BACKEND", None)
    os.environ.pop("STRICT_DUAL_WRITE", None)


def _install_main_stubs(monkeypatch):
    calls = {}

    db_mod = types.ModuleType('javdb.storage.db.db')
    db_mod.HISTORY_DB_PATH = 'reports/history.db'
    db_mod.REPORTS_DB_PATH = 'reports/reports.db'
    db_mod.OPERATIONS_DB_PATH = 'reports/operations.db'

    config_helper = types.ModuleType('javdb.infra.config')
    config_helper.use_sqlite = lambda: True
    config_helper.cfg = lambda _key, default=None: default

    split_mod = types.ModuleType('javdb.migrations.tools.migrate_v6_to_v7_split')
    split_mod._normalize_three_dbs = lambda *_args, **_kwargs: None

    align_mod = types.ModuleType('javdb.migrations.tools.align_inventory_with_moviehistory')

    def _run_alignment(args):
        calls['align_args'] = args
        return 0

    align_mod.run_alignment = _run_alignment

    v7_mod = types.ModuleType('javdb.migrations.tools.migrate_v7_to_v8')
    v7_mod.backup_db_file = lambda *_args, **_kwargs: None
    v7_mod.run_actor_backfill = lambda *_args, **_kwargs: 0
    v7_mod.run_schema_migration = lambda **_kwargs: 0
    v7_mod.verify_v8_layout = lambda *_args, **_kwargs: True

    monkeypatch.setitem(sys.modules, 'javdb.storage.db.db', db_mod)
    monkeypatch.setitem(sys.modules, 'javdb.infra.config', config_helper)
    monkeypatch.setitem(sys.modules, 'javdb.migrations.tools.migrate_v6_to_v7_split', split_mod)
    monkeypatch.setitem(sys.modules, 'javdb.migrations.tools.align_inventory_with_moviehistory', align_mod)
    monkeypatch.setitem(sys.modules, 'javdb.migrations.tools.migrate_v7_to_v8', v7_mod)

    return calls


def test_main_align_inventory_defaults_to_proxy(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].use_proxy is True


def test_main_align_inventory_no_proxy(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history', '--align-no-proxy'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].use_proxy is False


def test_main_align_inventory_rejects_conflicting_proxy_flags(monkeypatch):
    _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'migrate_to_current.py',
            '--skip-schema',
            '--align-inventory-history',
            '--align-no-proxy',
            '--align-use-proxy',
        ],
    )

    with pytest.raises(SystemExit):
        migrate_to_current.main()


def test_main_align_no_login_passed_through(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history', '--align-no-login'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].no_login is True


def test_main_align_no_login_defaults_false(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].no_login is False


def test_main_align_shuffle_passed_through(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history', '--align-shuffle'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].shuffle is True


def test_main_align_shuffle_defaults_false(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--skip-schema', '--align-inventory-history'],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].shuffle is False


def test_main_align_limit_per_worker_passed_through(monkeypatch):
    calls = _install_main_stubs(monkeypatch)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'migrate_to_current.py',
            '--skip-schema',
            '--align-inventory-history',
            '--align-limit-per-worker',
            '12',
        ],
    )

    assert migrate_to_current.main() == 0
    assert calls['align_args'].limit_per_worker == 12


def _install_metadata_stub(monkeypatch, calls):
    """Stub ``backfill_movie_metadata`` and record its invocation."""
    meta_mod = types.ModuleType('javdb.migrations.tools.backfill_movie_metadata')

    def _run_backfill_metadata(args):
        calls['meta_args'] = args
        return 0

    meta_mod.run_backfill_metadata = _run_backfill_metadata
    monkeypatch.setitem(
        sys.modules, 'javdb.migrations.tools.backfill_movie_metadata', meta_mod,
    )


def test_main_backfill_metadata_d1_auto_skips_schema(monkeypatch):
    """Under STORAGE_BACKEND=d1 a --backfill-metadata run (no --skip-schema)
    must auto-skip the local schema migration instead of aborting on a
    missing reports/history.db, and still reach run_backfill_metadata.
    """
    calls = _install_main_stubs(monkeypatch)
    _install_metadata_stub(monkeypatch, calls)

    import javdb.migrations.tools.migrate_v7_to_v8 as v7_mod

    def _record_schema(**_kwargs):
        calls['schema_called'] = True
        return 0

    monkeypatch.setattr(v7_mod, 'run_schema_migration', _record_schema)
    monkeypatch.setenv('STORAGE_BACKEND', 'd1')
    monkeypatch.setattr(
        sys,
        'argv',
        ['migrate_to_current.py', '--backfill-metadata'],
    )

    assert migrate_to_current.main() == 0
    assert 'schema_called' not in calls
    assert 'meta_args' in calls
