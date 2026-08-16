"""Advisory lease guarding the destructive rclone dedup executor.

The executor drains every pending ``DedupRecords`` row, and DailyIngestion /
AdHocIngestion invoke it outside the ``rclone-manager`` GitHub concurrency
group — so two runs could purge the same paths. These tests pin both halves:
the lease primitive itself and the executor's use of it.
"""
# ruff: noqa: E402

import csv
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


def _pending_record(path, video_code='A-001', folder='/test/path'):
    """Persist one pending dedup record and return the CSV path."""
    append_dedup_record(
        path, DedupRecord(video_code, 's', 'sub', folder, 100, 'cat', 'r', 't', 'False', ''),
    )
    return path


def _write_dedup_csv_file(path, folders):
    """Write an actual on-disk dedup CSV, for ``from_file_only=True`` tests.

    ``append_dedup_record``/``mark_records_deleted`` are DB-only (the
    ``csv_path`` argument they take is a legacy no-op — see their
    docstrings), so file-only mode's read-from-disk contract has no other
    seeding path: it is ``load_dedup_csv(..., from_file_only=True)``'s only
    source. Completion is still recorded in the DB, but in file-only mode
    ``_execute_dedup_purge`` also rewrites this file in place at each
    renewal checkpoint, so a test may assert on either the purge call count
    or the re-read CSV.
    """
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(DedupRecord._fields))
        writer.writeheader()
        for i, folder in enumerate(folders):
            writer.writerow(DedupRecord(
                f'A-{i:03d}', 's', 'sub', folder, 100, 'cat', 'r', 't', 'False', '',
            )._asdict())
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


class TestLeaseRenewal:
    """A TTL bounds a crashed holder, not the work. Long purges renew instead."""

    def test_renew_extends_the_deadline_and_keeps_the_owner(self):
        held = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)

        renewed = advisory_lock.renew(
            held.lease, ttl_seconds=3600, now=T0 + timedelta(minutes=45),
        )

        assert renewed is not None
        assert renewed.owner == "run-a"
        assert renewed.expires_at > held.lease.expires_at
        assert _stored_value() == renewed.token

    def test_renewed_lease_is_not_stealable_at_the_original_deadline(self):
        """The double-purge race: without renewal the lease expires while its
        holder is still purging and a second run takes it over."""
        held = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        advisory_lock.renew(
            held.lease, ttl_seconds=3600, now=T0 + timedelta(minutes=45),
        )

        rival = advisory_lock.try_acquire(
            KEY, owner="run-b", now=T0 + timedelta(minutes=61),
        )

        assert rival.lease is None
        assert "run-a" in rival.holder

    def test_renew_of_an_already_stolen_lease_reports_the_loss(self, caplog):
        first = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        later = T0 + timedelta(hours=2)
        second = advisory_lock.try_acquire(KEY, owner="run-b", now=later)

        with caplog.at_level("ERROR"):
            assert advisory_lock.renew(first.lease, now=later) is None

        # The new holder's lease is untouched by the loser's renewal attempt.
        assert _stored_value() == second.lease.token
        assert "could not be renewed" in caplog.text
        assert "run-b" in caplog.text

    def test_only_the_renewed_token_releases_the_lock(self):
        held = advisory_lock.try_acquire(KEY, owner="run-a", ttl_seconds=3600, now=T0)
        renewed = advisory_lock.renew(
            held.lease, ttl_seconds=3600, now=T0 + timedelta(minutes=45),
        )

        # The pre-renewal token is dead — releasing it must not clear the row.
        assert advisory_lock.release(held.lease) is False
        assert _stored_value() == renewed.token
        assert advisory_lock.release(renewed) is True
        assert _stored_value() is None


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

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_purge_renews_the_lease_and_releases_the_renewed_token(
        self, mock_run, _mock_export, _mock_dn, monkeypatch, tmp_path,
    ):
        """A purge longer than the TTL must not leave its own lease stealable.
        With the renew interval at 0 every folder is a checkpoint."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        _pending_record(path, video_code='A-002', folder='/test/path-2')
        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        # Pin use_db_storage() True: an environment with storage off takes the
        # unlocked branch, where there is no lease to renew at all.
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        with patch.object(
            rm.advisory_lock, 'renew', side_effect=advisory_lock.renew,
        ) as spy_renew:
            result = rm.run_execute_from_csv(path, dry_run=False)

        assert result == 0
        assert spy_renew.call_count >= 2
        assert [r['is_deleted'] for r in load_dedup_csv(path)] == ['True', 'True']
        # The release used the renewed token, so the row really is gone.
        assert _stored_value(rm.DEDUP_EXECUTE_LOCK_KEY) is None

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_purge_stops_when_the_lease_is_lost_mid_run(
        self, mock_run, _mock_export, _mock_dn, monkeypatch, tmp_path,
    ):
        """Losing the lease means someone else may be purging the same paths —
        stop rather than delete alongside them."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        _pending_record(path, video_code='A-002', folder='/test/path-2')
        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        # Pin use_db_storage() True: an environment with storage off takes the
        # unlocked branch, which purges without ever consulting the lease.
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        # Spy on the logger rather than caplog: _execute_dedup_purge calls
        # setup_logging(), which re-points handlers and drops caplog's.
        with patch.object(rm.advisory_lock, 'renew', return_value=None), \
                patch.object(rm, 'logger', wraps=rm.logger) as spy_logger:
            rm.run_execute_from_csv(path, dry_run=False)

        mock_run.assert_not_called()
        assert [r['is_deleted'] for r in load_dedup_csv(path)] == ['False', 'False']
        assert any(
            'lease lost mid-purge' in str(call.args[0])
            for call in spy_logger.error.call_args_list
        ), spy_logger.error.call_args_list

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.spider.services.dedup_store.cleanup_deleted_records')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_lease_lost_mid_run_skips_retention_cleanup_but_still_exports(
        self, mock_run, mock_export, mock_cleanup, _mock_dn, monkeypatch, tmp_path,
    ):
        """Losing the lease mid-purge must not let this runner delete rows out
        from under whoever holds the lease now — cleanup_deleted_records is a
        destructive DB write and must be skipped. export_dedup_history only
        snapshots the DB to a report file, so it still runs either way."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        _pending_record(path, video_code='A-002', folder='/test/path-2')
        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        with patch.object(rm.advisory_lock, 'renew', return_value=None):
            rm.run_execute_from_csv(path, dry_run=False)

        mock_cleanup.assert_not_called()
        mock_export.assert_called_once()

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_purge_checkpoints_prior_paths_when_renew_raises(
        self, mock_run, _mock_export, _mock_dn, monkeypatch, tmp_path,
    ):
        """A transient D1/sqlite error out of renew() must not propagate: that
        would skip the mark_records_deleted() call below the loop entirely,
        losing the bookkeeping for every path already purged in this pass
        (they would stay 'pending' and be purged again next run)."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _pending_record(str(tmp_path / 'dedup.csv'))
        _pending_record(path, video_code='A-002', folder='/test/path-2')
        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        real_renew = advisory_lock.renew
        calls = {'n': 0}

        def _flaky_renew(lease, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                return real_renew(lease, **kwargs)
            raise RuntimeError('transient D1 error')

        with patch.object(rm.advisory_lock, 'renew', side_effect=_flaky_renew), \
                patch.object(rm, 'logger', wraps=rm.logger) as spy_logger:
            result = rm.run_execute_from_csv(path, dry_run=False)

        # Checkpoint 1 (real renewal) lets folder 1 purge; checkpoint 2 raises
        # and stops the pass before folder 2. Folder 1's purge must still be
        # persisted even though the pass ended on an error, not a clean stop.
        rows = load_dedup_csv(path)
        assert result == 0
        assert rows[0]['is_deleted'] == 'True'
        assert rows[1]['is_deleted'] == 'False'
        assert any(
            'renewal raised' in str(call.args[0])
            for call in spy_logger.error.call_args_list
        ), spy_logger.error.call_args_list

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_file_only_purge_reports_busy_when_lease_lost_mid_run(
        self, mock_run, _mock_export, _mock_dn, monkeypatch, tmp_path,
    ):
        """File-only mode has no shared queue for a lost-lease run to fall back
        on: a plan left incomplete by a mid-purge lease loss must be reported
        the same retryable way as never starting it (EXIT_LOCK_HELD) — even
        though folder 1 did purge, the file's plan as a whole did not finish."""
        mock_run.return_value = MagicMock(returncode=0)
        path = _write_dedup_csv_file(str(tmp_path / 'dedup.csv'), ['/test/path', '/test/path-2'])
        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        real_renew = advisory_lock.renew
        calls = {'n': 0}

        def _flaky_renew(lease, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                return real_renew(lease, **kwargs)
            return None

        with patch.object(rm.advisory_lock, 'renew', side_effect=_flaky_renew):
            result = rm.run_execute_from_csv(path, dry_run=False, from_file_only=True)

        # mark_records_deleted is DB-only and these records were seeded
        # straight into the file (bypassing the DB), so completion can only be
        # observed via how many paths were actually purged before the break.
        assert result == rm.EXIT_LOCK_HELD
        assert mock_run.call_count == 1

        # Retry: this CSV has no matching DedupRecords rows at all (seeded
        # straight into the file, not via the DB), so the DB cross-check on
        # load cannot help here — _execute_dedup_purge must instead have
        # rewritten path 1's is_deleted=True into this same file in place at
        # the checkpoint before it lost the lease, for exactly this case.
        second = rm.run_execute_from_csv(path, dry_run=False, from_file_only=True)

        assert second == 0
        assert mock_run.call_count == 2

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_file_only_retry_skips_paths_the_db_already_marked_deleted(
        self, mock_run, _mock_export, _mock_dn, monkeypatch, tmp_path,
    ):
        """mark_records_deleted() never rewrites the CSV (see its docstring),
        so a retry re-reads the identical file. Without a DB cross-check on
        load, a retry after a lease-loss EXIT_LOCK_HELD would re-purge
        whatever this same file already finished — the DB, kept current at
        each renewal checkpoint, must be the retry's authority on what is
        actually left. Seeds via the DB + a real export, matching how
        --dedup-csv is produced in production (export_dedup_history)."""
        from javdb.spider.services.dedup_store import export_dedup_db_to_csv

        mock_run.return_value = MagicMock(returncode=0)
        db_seed_path = str(tmp_path / 'unused.csv')  # append_dedup_record ignores this arg
        _pending_record(db_seed_path, video_code='A-001', folder='/test/path')
        _pending_record(db_seed_path, video_code='A-002', folder='/test/path-2')
        csv_path = str(tmp_path / 'dedup.csv')
        export_dedup_db_to_csv(csv_path)

        monkeypatch.setattr(rm, 'DEDUP_LEASE_RENEW_INTERVAL_SECONDS', 0)
        monkeypatch.setattr(rm, 'use_db_storage', lambda: True)

        real_renew = advisory_lock.renew
        calls = {'n': 0}

        def _flaky_renew(lease, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                return real_renew(lease, **kwargs)
            return None

        with patch.object(rm.advisory_lock, 'renew', side_effect=_flaky_renew):
            first = rm.run_execute_from_csv(csv_path, dry_run=False, from_file_only=True)

        assert first == rm.EXIT_LOCK_HELD
        assert mock_run.call_count == 1

        # Retry: the CSV file is byte-identical (never rewritten), but the
        # first path's DB row is already IsDeleted from the checkpoint flush
        # during the first attempt — only the second path should be attempted.
        second = rm.run_execute_from_csv(csv_path, dry_run=False, from_file_only=True)

        assert second == 0
        assert mock_run.call_count == 2

    @patch('javdb.integrations.rclone.manager.service.get_configured_drive_name', return_value='gdrive')
    @patch('javdb.integrations.rclone.manager.service.export_dedup_history')
    @patch('javdb.integrations.rclone.dedup.subprocess.run')
    def test_file_only_purges_a_fresh_duplicate_at_a_previously_purged_path(
        self, mock_run, _mock_export, _mock_dn, tmp_path,
    ):
        """uq_dedup_active_path only constrains IsDeleted=0 rows, so a path can
        legally carry both an old deleted row and a fresh pending one — a new
        duplicate landing where an earlier one was already purged. The DB
        cross-check on load must key on "does this path still have a pending
        DB row", not "does this path have any deleted DB row at all", or the
        historical deleted row hides the fresh pending one from the purge
        queue entirely and the run falsely reports success."""
        from javdb.spider.services.dedup_store import export_dedup_db_to_csv, mark_records_deleted

        mock_run.return_value = MagicMock(returncode=0)
        db_seed_path = str(tmp_path / 'unused.csv')
        _pending_record(db_seed_path, video_code='A-001', folder='/test/path')
        mark_records_deleted(db_seed_path, [('/test/path', '2026-01-01 00:00:00')])
        # A fresh duplicate lands at the same path after the earlier one was
        # purged — legal, since the unique constraint only covers pending rows.
        _pending_record(db_seed_path, video_code='A-002', folder='/test/path')

        csv_path = str(tmp_path / 'dedup.csv')
        export_dedup_db_to_csv(csv_path)

        result = rm.run_execute_from_csv(csv_path, dry_run=False, from_file_only=True)

        assert result == 0
        mock_run.assert_called_once()

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
