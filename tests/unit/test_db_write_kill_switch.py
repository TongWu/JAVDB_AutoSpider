"""Unit tests for the JAVDB_FORBID_DB_WRITES kill switch.

The kill switch is the root-cause fix for TestIngestion polluting D1 /
SQLite on every push.  When ``JAVDB_FORBID_DB_WRITES=1`` is exported:

* :func:`storage_backend` must return ``'sqlite'`` regardless of any
  vars / config that say ``dual`` or ``d1`` — so no D1 client is ever
  constructed.
* :func:`storage_mode` must return ``'csv'`` so ``use_db_storage()``
  evaluates to ``False`` and the spider's ``init_db`` /
  ``db_create_report_session`` block is skipped entirely.
* As belt-and-braces, ``db_create_report_session`` itself must raise
  ``RuntimeError`` if anything ever reaches it under the kill switch
  (defence-in-depth against future regressions).
"""

from __future__ import annotations

import os

import pytest

import utils.infra.db as db_mod
from javdb.infra import config as config_helper


@pytest.fixture
def kill_switch(monkeypatch: pytest.MonkeyPatch):
    """Engage the kill switch for the duration of the test."""
    monkeypatch.setenv('JAVDB_FORBID_DB_WRITES', '1')
    yield


@pytest.fixture
def kill_switch_off(monkeypatch: pytest.MonkeyPatch):
    """Make sure the kill switch is OFF (some tests verify the negative)."""
    monkeypatch.delenv('JAVDB_FORBID_DB_WRITES', raising=False)
    yield


class TestKillSwitchHelpers:
    def test_db_writes_forbidden_is_off_by_default(self, kill_switch_off):
        assert config_helper.db_writes_forbidden() is False

    @pytest.mark.parametrize('value', ['1', 'true', 'True', 'YES', 'on'])
    def test_db_writes_forbidden_accepts_truthy(
        self, monkeypatch, value
    ):
        monkeypatch.setenv('JAVDB_FORBID_DB_WRITES', value)
        assert config_helper.db_writes_forbidden() is True

    @pytest.mark.parametrize('value', ['', '0', 'false', 'no', 'off'])
    def test_db_writes_forbidden_rejects_falsy(
        self, monkeypatch, value
    ):
        monkeypatch.setenv('JAVDB_FORBID_DB_WRITES', value)
        assert config_helper.db_writes_forbidden() is False


class TestKillSwitchOverridesStorageResolution:
    def test_storage_backend_forced_to_sqlite_under_kill_switch(
        self, kill_switch, monkeypatch
    ):
        # Even if vars/env say dual, kill switch wins.
        monkeypatch.setenv('STORAGE_BACKEND', 'dual')
        assert config_helper.storage_backend() == 'sqlite'

    def test_storage_backend_forced_to_sqlite_for_d1_too(
        self, kill_switch, monkeypatch
    ):
        monkeypatch.setenv('STORAGE_BACKEND', 'd1')
        assert config_helper.storage_backend() == 'sqlite'

    def test_storage_mode_forced_to_csv_under_kill_switch(
        self, kill_switch, monkeypatch
    ):
        monkeypatch.setenv('VAR_STORAGE_MODE', 'duo')
        assert config_helper.storage_mode() == 'csv'

    def test_use_db_storage_is_false_under_kill_switch(
        self, kill_switch, monkeypatch
    ):
        monkeypatch.setenv('STORAGE_BACKEND', 'dual')
        monkeypatch.setenv('VAR_STORAGE_MODE', 'duo')
        assert config_helper.use_db_storage() is False


class TestKillSwitchBlocksDbCreateReportSession:
    def test_db_create_report_session_raises_under_kill_switch(
        self, kill_switch
    ):
        with pytest.raises(RuntimeError) as exc_info:
            db_mod.db_create_report_session(
                report_type='daily',
                report_date='2026-05-08',
                csv_filename='kill-switch.csv',
                run_id='kill-run',
                run_attempt=1,
            )
        assert 'JAVDB_FORBID_DB_WRITES' in str(exc_info.value)

    def test_db_create_report_session_works_when_switch_off(
        self, kill_switch_off, monkeypatch
    ):
        # Pin the backend to sqlite so this test never tries to construct
        # a D1 client off whatever STORAGE_BACKEND happens to be exported
        # in the surrounding environment (e.g., a developer with
        # STORAGE_BACKEND=dual, or CI runs that pre-set it).  The kill
        # switch itself isn't engaged here, so storage_backend() falls
        # through to the env var resolution path.
        monkeypatch.setenv('STORAGE_BACKEND', 'sqlite')
        sid = db_mod.db_create_report_session(
            report_type='daily',
            report_date='2026-05-08',
            csv_filename='kill-switch-off.csv',
            run_id='non-kill-run',
            run_attempt=1,
        )
        assert isinstance(sid, str) and db_mod._SESSION_ID_PATTERN.match(sid)
