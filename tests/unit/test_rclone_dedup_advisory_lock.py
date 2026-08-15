"""Advisory lease guarding the destructive rclone dedup executor.

The executor drains every pending ``DedupRecords`` row, and DailyIngestion /
AdHocIngestion invoke it outside the ``rclone-manager`` GitHub concurrency
group — so two runs could purge the same paths. These tests pin both halves:
the lease primitive itself and the executor's use of it.
"""
# ruff: noqa: E402

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.storage import advisory_lock
import javdb.integrations.rclone.manager.service as rm
from javdb.spider.services.dedup_store import append_dedup_record, load_dedup_csv
from javdb.spider.services.dedup_types import DedupRecord

T0 = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)
KEY = "lock:test_dedup_execute"


def _pending_record(path):
    """Persist one pending dedup record and return the CSV path."""
    append_dedup_record(
        path, DedupRecord('A-001', 's', 'sub', '/test/path', 100, 'cat', 'r', 't', 'False', ''),
    )
    return path


def _stored_value(key=KEY):
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db

    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        ).fetchone()
    return None if row is None else row[0]


# ============================================================================
# Lease primitive
# ============================================================================

class TestLeaseAcquisition:
    def test_acquire_when_free(self):
        attempt = advisory_lock.try_acquire(KEY, owner="run-a", now=T0)

        assert attempt.holder is None
        assert attempt.lease is not None
        assert attempt.lease.owner == "run-a"
        # Default TTL must comfortably outlive a long dedup run.
        assert advisory_lock.DEFAULT_LEASE_TTL_SECONDS >= 6 * 60 * 60
        assert _stored_value() == attempt.lease.token

    def test_second_acquire_blocked_while_held(self):
        first = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        assert first.lease is not None

        second = advisory_lock.try_acquire(
            KEY, owner="run-b", now=T0 + timedelta(minutes=30),
        )

        assert second.lease is None
        assert "run-a" in second.holder
        # The live lease is untouched — the loser must not overwrite it.
        assert _stored_value() == first.lease.token

    def test_acquire_succeeds_after_expiry_and_logs_the_steal(self, caplog):
        first = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)

        with caplog.at_level("WARNING"):
            second = advisory_lock.try_acquire(
                KEY, owner="run-b", now=T0 + timedelta(hours=2),
            )

        assert second.lease is not None
        assert second.lease.owner == "run-b"
        assert _stored_value() == second.lease.token
        stolen = [r for r in caplog.records if "Stole expired advisory lease" in r.message]
        assert stolen, caplog.text
        assert "run-a" in stolen[0].getMessage()
        assert first.lease.expires_at in stolen[0].getMessage()

    def test_expired_lease_is_stolen_only_once(self):
        advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        later = T0 + timedelta(hours=2)

        winner = advisory_lock.try_acquire(KEY, owner="run-b", now=later)
        loser = advisory_lock.try_acquire(KEY, owner="run-c", now=later)

        assert winner.lease is not None
        assert loser.lease is None
        assert "run-b" in loser.holder


class TestLeaseRelease:
    def test_release_by_owner_clears_the_row(self):
        attempt = advisory_lock.try_acquire(KEY, owner="run-a", now=T0)

        assert advisory_lock.release(attempt.lease) is True
        assert _stored_value() is None
        # The lock is free again immediately.
        assert advisory_lock.try_acquire(KEY, owner="run-b", now=T0).lease is not None

    def test_release_by_other_owner_is_refused(self):
        held = advisory_lock.try_acquire(KEY, owner="run-a", now=T0)
        imposter = advisory_lock.Lease(key=KEY, owner="run-b", expires_at=held.lease.expires_at)

        assert advisory_lock.release(imposter) is False
        assert _stored_value() == held.lease.token

    def test_release_of_stolen_lease_does_not_evict_the_new_holder(self):
        first = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        second = advisory_lock.try_acquire(
            KEY, owner="run-b", now=T0 + timedelta(hours=2),
        )

        # The slow original run finally finishes and releases its dead lease.
        assert advisory_lock.release(first.lease) is False
        assert _stored_value() == second.lease.token


class TestOwnerIdentity:
    def test_owner_uses_github_run_identity(self, monkeypatch):
        monkeypatch.setenv("GITHUB_RUN_ID", "12345")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        assert advisory_lock.default_owner() == "gha-12345-2"

    def test_owner_falls_back_to_host_and_pid(self, monkeypatch):
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
        assert advisory_lock.default_owner().endswith(f"-{os.getpid()}")


def _install_operations_conn(monkeypatch, conn):
    """Point the lock's operations-DB handle at *conn*."""
    import contextlib

    @contextlib.contextmanager
    def _open():
        yield conn

    monkeypatch.setattr(advisory_lock, '_operations_db', _open)
    return conn


class _FakeD1Connection:
    """Mimics ``D1Connection``: dict rows and rowcount from ``meta.changes``.

    The conditional upsert mutates ``_stored`` when the fake reports one changed
    row, so the read-back inside :func:`advisory_lock.try_acquire` sees what a
    real D1 would serve immediately afterwards (each statement auto-commits).
    """

    def __init__(self, changes, stored=None):
        self._changes = changes
        self._stored = stored
        self.statements = []

    def execute(self, sql, params=()):
        from javdb.storage.d1_client import D1Cursor

        self.statements.append((sql, params))
        stripped = sql.strip()
        if stripped.startswith("SELECT"):
            results = [{"value": self._stored}] if self._stored else []
            return D1Cursor({"results": results, "meta": {}})
        if stripped.startswith("INSERT") and self._changes == 1:
            self._stored = params[1]
        return D1Cursor({"meta": {"changes": self._changes}})


class _FakeCursor:
    """Minimal cursor: a rowcount for writes, one dict row for reads."""

    def __init__(self, rows=(), rowcount=-1):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDualConnection:
    """Mimics ``DualConnection`` under ``STORAGE_BACKEND=dual``.

    A write's ``rowcount`` is the *local SQLite mirror's*
    (``DualCursor.rowcount``, dual_connection.py:384-386) and is hard-coded to 1
    here: each runner owns its mirror file, so a runner's own conditional upsert
    always lands locally no matter who won on D1. Every SELECT is served by D1
    alone (``DualConnection.execute``, dual_connection.py:621-622), so the
    read-back is what reveals the real winner.
    """

    def __init__(self, d1_holder_token=None):
        # Non-None → another runner already holds a live lease on D1, so our
        # conditional upsert changes nothing there.
        self._d1_value = d1_holder_token
        self._d1_locked = d1_holder_token is not None
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if sql.strip().startswith("SELECT"):
            rows = [{"value": self._d1_value}] if self._d1_value else []
            return _FakeCursor(rows=rows)
        if not self._d1_locked:
            self._d1_value = params[1]
        return _FakeCursor(rowcount=1)


@pytest.fixture
def fake_d1(monkeypatch):
    """Route the lock at a fake D1 connection built by the test."""

    def _install(changes, stored=None):
        return _install_operations_conn(
            monkeypatch, _FakeD1Connection(changes, stored=stored),
        )

    return _install


class TestD1AcquireContract:
    """On D1 each statement auto-commits server-side, so the row written by the
    conditional upsert is immediately visible to the verifying read-back."""

    def test_applied_upsert_acquires(self, fake_d1):
        fake_d1(changes=1)
        assert advisory_lock.try_acquire(KEY, owner="run-a", now=T0).lease is not None

    def test_blocked_upsert_names_the_dict_row_holder(self, fake_d1):
        fake_d1(changes=0, stored="2026-08-12T20:00:00Z|run-a")

        attempt = advisory_lock.try_acquire(KEY, owner="run-b", now=T0)

        assert attempt.lease is None
        assert "run-a" in attempt.holder
        assert "2026-08-12T20:00:00Z" in attempt.holder

    def test_absent_changes_field_reads_as_not_acquired(self, fake_d1):
        """A response without ``meta.changes`` must fail safe, never purge — the
        read-back finds no row bearing our token, so nothing is claimed."""
        fake_d1(changes=None)
        assert advisory_lock.try_acquire(KEY, owner="run-a", now=T0).lease is None

    def test_release_needs_a_deleted_row(self, fake_d1):
        lease = advisory_lock.Lease(key=KEY, owner="run-a", expires_at="2026-08-12T16:00:00Z")

        fake_d1(changes=0)
        assert advisory_lock.release(lease) is False
        conn = fake_d1(changes=1)
        assert advisory_lock.release(lease) is True
        assert conn.statements[0][1] == (KEY, lease.token)


class TestDualMirrorRowCountIsNotAuthoritative:
    """Under ``STORAGE_BACKEND=dual`` the upsert's rowcount comes from this
    runner's own SQLite mirror, and every runner has a different mirror file —
    so two runners can both see 1 while only one won on D1. The verdict must
    come from reading the row back off D1."""

    def test_mirror_rowcount_one_does_not_grant_another_runners_lease(
        self, monkeypatch, caplog,
    ):
        conn = _install_operations_conn(
            monkeypatch, _FakeDualConnection("2026-08-12T20:00:00Z|run-winner"),
        )

        with caplog.at_level("WARNING"):
            attempt = advisory_lock.try_acquire(KEY, owner="run-loser", now=T0)

        # The upsert was issued and the local mirror reported a changed row …
        assert any("INSERT INTO system_state" in sql for sql, _p in conn.statements)
        assert "rowcount=1" in caplog.text, caplog.text
        # … yet D1 still holds the winner's token, so we stand down.
        assert attempt.lease is None
        assert "run-winner" in attempt.holder

    def test_read_back_of_our_own_token_grants_the_lease(self, monkeypatch):
        _install_operations_conn(monkeypatch, _FakeDualConnection())

        attempt = advisory_lock.try_acquire(KEY, owner="run-a", now=T0)

        assert attempt.holder is None
        assert attempt.lease is not None
        assert attempt.lease.owner == "run-a"


# ============================================================================
# Executor integration
# ============================================================================

class TestExecutorLocking:
    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_skips_without_draining_when_lease_held(self, mock_run, _mock_dn, tmp_path, caplog):
        """Shared-queue mode: the holder drains the very rows this run would
        have, so the work happens and a clean 0 keeps scheduled pipelines green.
        """
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        held = advisory_lock.try_acquire(rm.DEDUP_EXECUTE_LOCK_KEY, owner="other-run")
        assert held.lease is not None

        with caplog.at_level("WARNING"):
            result = rm.run_execute_from_csv(path, dry_run=False, from_file_only=False)

        assert result == 0
        mock_run.assert_not_called()
        assert load_dedup_csv(path)[0]['is_deleted'] == 'False'
        assert "EXECUTE SKIPPED" in caplog.text
        assert "other-run" in caplog.text
        # The holder's lease survives the loser's attempt.
        assert _stored_value(rm.DEDUP_EXECUTE_LOCK_KEY) == held.lease.token

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_file_only_skip_reports_busy_rather_than_success(
        self, mock_run, _mock_dn, tmp_path, caplog,
    ):
        """File-only mode (``--dedup-csv``): the holder drains the shared queue,
        never this file's plan — so nobody executes it. Exit 0 would report
        success for work that never ran; the caller needs a retryable signal."""
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        held = advisory_lock.try_acquire(rm.DEDUP_EXECUTE_LOCK_KEY, owner="other-run")
        assert held.lease is not None

        with caplog.at_level("WARNING"):
            result = rm.run_execute_from_csv(path, dry_run=False, from_file_only=True)

        assert rm.EXIT_LOCK_HELD != 0
        assert result == rm.EXIT_LOCK_HELD
        mock_run.assert_not_called()
        assert load_dedup_csv(path)[0]['is_deleted'] == 'False'
        assert "was NOT executed" in caplog.text
        assert "Retry after the holder finishes" in caplog.text
        # The holder's lease survives the loser's attempt.
        assert _stored_value(rm.DEDUP_EXECUTE_LOCK_KEY) == held.lease.token

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_warns_that_lease_is_machine_local_under_sqlite(
        self, mock_run, _mock_export, _mock_dn, tmp_path, caplog,
    ):
        """A sqlite operations.db is this machine's own file, so the lease gives
        no cross-runner exclusion. Say so loudly — but still run: a
        machine-local lease is genuinely useful to a single-machine
        self-hoster, and production runs d1 where the lease is shared."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))

        with patch('javdb.storage.db.current_backend', return_value='sqlite'):
            with caplog.at_level("WARNING"):
                result = rm.run_execute_from_csv(path, dry_run=False)

        assert "MACHINE-LOCAL" in caplog.text, caplog.text
        assert "STORAGE_BACKEND=d1" in caplog.text
        # Degradation is announced, not enforced — the purge still happened.
        assert result == 0
        assert load_dedup_csv(path)[0]['is_deleted'] == 'True'

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_no_machine_local_warning_under_d1(
        self, mock_run, _mock_export, _mock_dn, tmp_path, caplog,
    ):
        """Guards the test above from passing vacuously: on d1 the lease really
        is shared, so the warning must not fire."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))

        with patch('javdb.storage.db.current_backend', return_value='d1'):
            with caplog.at_level("WARNING"):
                result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 0
        assert "MACHINE-LOCAL" not in caplog.text

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_skips_when_dual_read_back_names_another_holder(
        self, mock_run, _mock_dn, monkeypatch, tmp_path, caplog,
    ):
        """Dual mode: our own SQLite mirror accepted the upsert (rowcount 1) but
        D1 — the canonical side — shows another runner holds the lease. The
        executor must skip, or both runners drain the same pending records."""
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        _install_operations_conn(
            monkeypatch, _FakeDualConnection("2026-08-12T20:00:00Z|run-winner"),
        )

        with caplog.at_level("WARNING"):
            result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 0
        mock_run.assert_not_called()
        assert load_dedup_csv(path)[0]['is_deleted'] == 'False'
        assert "EXECUTE SKIPPED" in caplog.text
        assert "run-winner" in caplog.text

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_purges_and_releases_on_success(self, mock_run, _mock_export, _mock_dn, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))

        with patch.object(
            rm.advisory_lock, 'release', side_effect=advisory_lock.release,
        ) as spy_release:
            result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 0
        assert load_dedup_csv(path)[0]['is_deleted'] == 'True'
        # Proves the run really held a lease rather than skipping the lock.
        assert spy_release.call_count == 1
        assert _stored_value(rm.DEDUP_EXECUTE_LOCK_KEY) is None

    def test_releases_lease_when_execution_raises(self, tmp_path):
        boom = RuntimeError('purge exploded')
        # Pin use_db_storage() True and spy on release: without both, an
        # environment where storage is off would take the unlocked branch and
        # this test would pass vacuously (the lease row is trivially absent).
        real_release = advisory_lock.release
        with patch.object(rm, 'use_db_storage', return_value=True), \
                patch.object(rm.advisory_lock, 'release',
                             side_effect=real_release) as spy_release, \
                patch.object(rm, '_execute_dedup_purge', side_effect=boom):
            with pytest.raises(RuntimeError, match='purge exploded'):
                rm.run_execute_from_csv(str(tmp_path / 'dedup.csv'), dry_run=False)

        assert spy_release.call_count == 1
        assert _stored_value(rm.DEDUP_EXECUTE_LOCK_KEY) is None

    def test_dry_run_takes_no_lease(self, tmp_path):
        with patch.object(rm.advisory_lock, 'try_acquire') as mock_acquire:
            result = rm.run_execute_from_csv(str(tmp_path / 'missing.csv'), dry_run=True)

        assert result == 0
        mock_acquire.assert_not_called()

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_runs_unlocked_when_db_storage_disabled(
        self, mock_run, _mock_export, _mock_dn, tmp_path,
    ):
        """CSV-only / JAVDB_FORBID_DB_WRITES: taking the lease would itself be a
        forbidden DB write, and there is no shared pending queue to race over."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))

        with patch.object(rm, 'use_db_storage', return_value=False):
            with patch.object(rm.advisory_lock, 'try_acquire') as mock_acquire:
                result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 0
        mock_acquire.assert_not_called()
        mock_run.assert_called()

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_fails_closed_when_lock_storage_is_broken(self, mock_run, _mock_dn, tmp_path, caplog):
        """Without a usable lock row the executor must refuse to purge: purging
        first and then failing to mark the rows deleted would leave them pending
        for the next run — the double purge the lease exists to prevent."""
        from javdb.storage.db import OPERATIONS_DB_PATH, get_db

        path = _pending_record(str(tmp_path / 'dedup.csv'))
        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute("DROP TABLE system_state")

        with caplog.at_level("ERROR"):
            result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 1
        mock_run.assert_not_called()
        assert load_dedup_csv(path)[0]['is_deleted'] == 'False'
        assert "refusing to purge" in caplog.text
