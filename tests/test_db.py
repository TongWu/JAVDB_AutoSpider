"""Tests for utils/db.py — SQLite database layer."""

import os
import sys
import sqlite3

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pytest
import utils.db as db_mod


# ── init / schema ─────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_tables(self, _isolate_sqlite):
        with db_mod.get_db(_isolate_sqlite) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
        expected = {
            'dedup_records', 'parsed_movies_history', 'pikpak_history',
            'pikpak_stats', 'proxy_bans', 'rclone_inventory',
            'report_rows', 'report_sessions', 'schema_version',
            'spider_stats', 'uploader_stats',
        }
        assert expected.issubset(set(tables))

    def test_wal_mode(self, _isolate_sqlite):
        with db_mod.get_db(_isolate_sqlite) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == 'wal'

    def test_idempotent(self, _isolate_sqlite):
        db_mod.init_db(_isolate_sqlite)
        db_mod.init_db(_isolate_sqlite)

    def test_init_db_noop_in_csv_mode(self, tmp_path, storage_mode_csv):
        """init_db should be a no-op when STORAGE_MODE='csv'."""
        fresh_db = str(tmp_path / "should_not_exist.db")
        db_mod.init_db(fresh_db)
        assert not os.path.exists(fresh_db)


# ── parsed_movies_history ─────────────────────────────────────────────────

class TestHistory:
    def _upsert(self, href='/v/ABC-123', phase=1, code='ABC-123', magnets=None):
        db_mod.db_upsert_history(href, phase, code, magnet_links=magnets)

    def test_upsert_and_load(self, _isolate_sqlite):
        self._upsert()
        history = db_mod.db_load_history()
        assert '/v/ABC-123' in history
        assert history['/v/ABC-123']['video_code'] == 'ABC-123'

    def test_upsert_updates_existing(self, _isolate_sqlite):
        self._upsert()
        self._upsert(magnets={'subtitle': 'magnet:new'})
        history = db_mod.db_load_history()
        assert len(history) == 1
        assert 'magnet:new' in history['/v/ABC-123'].get('subtitle', '')

    def test_multiple_records(self, _isolate_sqlite):
        self._upsert(href='/v/A', code='A')
        self._upsert(href='/v/B', code='B')
        history = db_mod.db_load_history()
        assert len(history) == 2

    def test_check_torrent_in_history(self, _isolate_sqlite):
        self._upsert(magnets={'subtitle': 'magnet:?xt=urn:btih:test123'})
        assert db_mod.db_check_torrent_in_history('/v/ABC-123', 'subtitle') is True
        assert db_mod.db_check_torrent_in_history('/v/ABC-123', 'hacked_subtitle') is False
        assert db_mod.db_check_torrent_in_history('/v/XXX-999', 'subtitle') is False

    def test_batch_update_last_visited(self, _isolate_sqlite):
        self._upsert(href='/v/A', code='A')
        self._upsert(href='/v/B', code='B')
        db_mod.db_batch_update_last_visited(['/v/A'])
        history = db_mod.db_load_history()
        assert history['/v/A']['last_visited_datetime'] != ''

    def test_get_all_history_records(self, _isolate_sqlite):
        self._upsert(href='/v/A', code='A')
        all_recs = db_mod.db_get_all_history_records()
        assert len(all_recs) == 1


# ── rclone_inventory ──────────────────────────────────────────────────────

class TestRcloneInventory:
    def _entry(self, code='TEST-001', **kw):
        row = {
            'video_code': code, 'sensor_category': '有码',
            'subtitle_category': '中字', 'folder_path': f'remote:/{code}',
            'folder_size': 1024, 'file_count': 3, 'scan_datetime': '2024-01-01',
        }
        row.update(kw)
        return row

    def test_replace_and_load(self, _isolate_sqlite):
        entries = [self._entry('A'), self._entry('B')]
        db_mod.db_replace_rclone_inventory(entries)
        inv = db_mod.db_load_rclone_inventory()
        assert 'A' in inv
        assert 'B' in inv

    def test_replace_clears_old(self, _isolate_sqlite):
        db_mod.db_replace_rclone_inventory([self._entry('OLD')])
        db_mod.db_replace_rclone_inventory([self._entry('NEW')])
        inv = db_mod.db_load_rclone_inventory()
        assert 'OLD' not in inv
        assert 'NEW' in inv

    def test_multiple_entries_same_code(self, _isolate_sqlite):
        entries = [
            self._entry('SAME', folder_path='remote:/copy1'),
            self._entry('SAME', folder_path='remote:/copy2'),
        ]
        db_mod.db_replace_rclone_inventory(entries)
        inv = db_mod.db_load_rclone_inventory()
        assert len(inv['SAME']) == 2


# ── dedup_records ─────────────────────────────────────────────────────────

class TestDedupRecords:
    def _rec(self, code='DUP-001', **kw):
        row = {
            'video_code': code, 'existing_sensor': '有码',
            'existing_subtitle': '无字', 'existing_gdrive_path': f'remote:/{code}',
            'existing_folder_size': 2048, 'new_torrent_category': '有码',
            'deletion_reason': 'Subtitle upgrade', 'detect_datetime': '2024-01-01',
            'is_deleted': 0, 'delete_datetime': '',
        }
        row.update(kw)
        return row

    def test_append_and_load(self, _isolate_sqlite):
        db_mod.db_append_dedup_record(self._rec())
        rows = db_mod.db_load_dedup_records()
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'DUP-001'

    def test_save_overwrites(self, _isolate_sqlite):
        db_mod.db_append_dedup_record(self._rec('A'))
        db_mod.db_append_dedup_record(self._rec('B'))
        rows = db_mod.db_load_dedup_records()
        assert len(rows) == 2

        rows[0]['is_deleted'] = 1
        rows[0]['delete_datetime'] = '2024-06-01'
        db_mod.db_save_dedup_records(rows)

        reloaded = db_mod.db_load_dedup_records()
        assert len(reloaded) == 2
        deleted = [r for r in reloaded if r.get('is_deleted') in (1, True)]
        assert len(deleted) == 1

    def test_append_skips_duplicate_pending(self, _isolate_sqlite):
        """Same existing_gdrive_path with is_deleted=0 should be rejected."""
        r = self._rec('A', existing_gdrive_path='remote:/dup_path')
        assert db_mod.db_append_dedup_record(r) > 0
        assert db_mod.db_append_dedup_record(r) == -1
        assert len(db_mod.db_load_dedup_records()) == 1

    def test_append_allows_after_deleted(self, _isolate_sqlite):
        """A deleted record should not block re-append of the same path."""
        r = self._rec('A', existing_gdrive_path='remote:/path')
        db_mod.db_append_dedup_record(r)
        db_mod.db_mark_records_deleted([('remote:/path', '2024-06-01 00:00:00')])
        assert db_mod.db_append_dedup_record(r) > 0
        assert len(db_mod.db_load_dedup_records()) == 2

    def test_mark_records_deleted(self, _isolate_sqlite):
        db_mod.db_append_dedup_record(self._rec('A'))
        db_mod.db_append_dedup_record(self._rec('B'))
        updated = db_mod.db_mark_records_deleted([
            ('remote:/A', '2024-06-01 10:00:00'),
        ])
        assert updated == 1
        rows = db_mod.db_load_dedup_records()
        a_row = [r for r in rows if r['video_code'] == 'A'][0]
        b_row = [r for r in rows if r['video_code'] == 'B'][0]
        assert a_row['is_deleted'] == 1
        assert a_row['delete_datetime'] == '2024-06-01 10:00:00'
        assert b_row['is_deleted'] == 0

    def test_mark_multiple_pending_same_path(self, _isolate_sqlite):
        """Edge 1a: multiple pending records with the same path."""
        r1 = self._rec('A', existing_gdrive_path='remote:/same')
        db_mod.db_append_dedup_record(r1)
        # Force a second record with the same path by marking the first deleted first
        db_mod.db_mark_records_deleted([('remote:/same', '2024-01-01 00:00:00')])
        db_mod.db_append_dedup_record(r1)
        # Now re-mark: only the second (pending) row should update
        updated = db_mod.db_mark_records_deleted([('remote:/same', '2024-06-01 00:00:00')])
        assert updated == 1

    def test_mark_idempotent(self, _isolate_sqlite):
        """Marking an already-deleted record should not update again."""
        db_mod.db_append_dedup_record(self._rec('A'))
        db_mod.db_mark_records_deleted([('remote:/A', '2024-06-01 00:00:00')])
        updated = db_mod.db_mark_records_deleted([('remote:/A', '2024-07-01 00:00:00')])
        assert updated == 0

    def test_cleanup_deleted_records(self, _isolate_sqlite):
        db_mod.db_append_dedup_record(self._rec('OLD'))
        db_mod.db_mark_records_deleted([('remote:/OLD', '2020-01-01 00:00:00')])
        db_mod.db_append_dedup_record(self._rec('FRESH'))
        db_mod.db_mark_records_deleted([('remote:/FRESH', '2099-01-01 00:00:00')])
        db_mod.db_append_dedup_record(self._rec('PENDING'))

        removed = db_mod.db_cleanup_deleted_records(older_than_days=30)
        assert removed == 1
        rows = db_mod.db_load_dedup_records()
        codes = {r['video_code'] for r in rows}
        assert 'OLD' not in codes
        assert 'FRESH' in codes
        assert 'PENDING' in codes

    def test_cleanup_skips_empty_delete_datetime(self, _isolate_sqlite):
        """Edge 3a: is_deleted=1 but delete_datetime='' should be kept."""
        r = self._rec('ANOMALY', is_deleted=1, delete_datetime='')
        db_mod.db_append_dedup_record(r)
        removed = db_mod.db_cleanup_deleted_records(older_than_days=0)
        assert removed == 0
        assert len(db_mod.db_load_dedup_records()) == 1

    def test_cleanup_zero_retention(self, _isolate_sqlite):
        """Edge 3b: retention_days=0 removes all with valid timestamps."""
        db_mod.db_append_dedup_record(self._rec('A'))
        db_mod.db_mark_records_deleted([('remote:/A', '2024-06-01 00:00:00')])
        removed = db_mod.db_cleanup_deleted_records(older_than_days=0)
        assert removed == 1
        assert len(db_mod.db_load_dedup_records()) == 0


# ── pikpak_history ────────────────────────────────────────────────────────

class TestPikpakHistory:
    def test_append(self, _isolate_sqlite):
        rec = {
            'torrent_hash': 'abc123', 'torrent_name': 'test.torrent',
            'category': 'cat', 'magnet_uri': 'magnet:?xt=urn:btih:abc',
            'added_to_qb_date': '2024-01-01', 'deleted_from_qb_date': '',
            'uploaded_to_pikpak_date': '', 'transfer_status': 'success',
            'error_message': '',
        }
        db_mod.db_append_pikpak_history(rec)

        with db_mod.get_db(_isolate_sqlite) as conn:
            rows = conn.execute("SELECT * FROM pikpak_history").fetchall()
        assert len(rows) == 1
        assert dict(rows[0])['torrent_hash'] == 'abc123'


# ── proxy_bans ────────────────────────────────────────────────────────────

class TestProxyBans:
    def test_save_and_load(self, _isolate_sqlite):
        bans = [
            {'proxy_name': 'proxy1', 'ban_time': '2024-01-01', 'unban_time': '2024-01-02'},
            {'proxy_name': 'proxy2', 'ban_time': '2024-01-03', 'unban_time': '2024-01-04'},
        ]
        db_mod.db_save_proxy_bans(bans)
        loaded = db_mod.db_load_proxy_bans()
        assert len(loaded) == 2
        names = {r['proxy_name'] for r in loaded}
        assert names == {'proxy1', 'proxy2'}

    def test_save_replaces(self, _isolate_sqlite):
        db_mod.db_save_proxy_bans([
            {'proxy_name': 'old', 'ban_time': 't1', 'unban_time': 't2'}
        ])
        db_mod.db_save_proxy_bans([
            {'proxy_name': 'new', 'ban_time': 't3', 'unban_time': 't4'}
        ])
        loaded = db_mod.db_load_proxy_bans()
        assert len(loaded) == 1
        assert loaded[0]['proxy_name'] == 'new'


# ── report_sessions + report_rows ─────────────────────────────────────────

class TestReportSessions:
    def test_create_session(self, _isolate_sqlite):
        sid = db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='test.csv', db_path=_isolate_sqlite,
        )
        assert sid is not None
        assert isinstance(sid, int)

    def test_duplicate_csv_filename_allowed(self, _isolate_sqlite):
        sid1 = db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='same.csv', db_path=_isolate_sqlite,
        )
        sid2 = db_mod.db_create_report_session(
            report_type='daily', report_date='20240102',
            csv_filename='same.csv', db_path=_isolate_sqlite,
        )
        assert sid1 != sid2

    def test_insert_and_get_rows(self, _isolate_sqlite):
        sid = db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='rows_test.csv', db_path=_isolate_sqlite,
        )
        rows = [
            {'href': '/v/A', 'video_code': 'A', 'page': '1', 'actor': 'Actor1',
             'rate': '4.5', 'comment_number': '100'},
            {'href': '/v/B', 'video_code': 'B', 'page': '2', 'actor': 'Actor2',
             'rate': '', 'comment_number': ''},
        ]
        count = db_mod.db_insert_report_rows(sid, rows, db_path=_isolate_sqlite)
        assert count == 2

        loaded = db_mod.db_get_report_rows(sid, db_path=_isolate_sqlite)
        assert len(loaded) == 2
        assert loaded[0]['video_code'] == 'A'
        assert loaded[1]['video_code'] == 'B'

    def test_get_latest_session(self, _isolate_sqlite):
        db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='first.csv', db_path=_isolate_sqlite,
        )
        sid2 = db_mod.db_create_report_session(
            report_type='adhoc', report_date='20240102',
            csv_filename='second.csv', db_path=_isolate_sqlite,
        )
        latest = db_mod.db_get_latest_session(db_path=_isolate_sqlite)
        assert latest['id'] == sid2

        latest_daily = db_mod.db_get_latest_session(report_type='daily', db_path=_isolate_sqlite)
        assert latest_daily['csv_filename'] == 'first.csv'

    def test_get_sessions_by_date(self, _isolate_sqlite):
        db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='d1.csv', db_path=_isolate_sqlite,
        )
        db_mod.db_create_report_session(
            report_type='adhoc', report_date='20240101',
            csv_filename='a1.csv', db_path=_isolate_sqlite,
        )
        sessions = db_mod.db_get_sessions_by_date('20240101', db_path=_isolate_sqlite)
        assert len(sessions) == 2


# ── stats tables ──────────────────────────────────────────────────────────

class TestStats:
    @pytest.fixture
    def session_id(self, _isolate_sqlite):
        return db_mod.db_create_report_session(
            report_type='daily', report_date='20240101',
            csv_filename='stats_test.csv', db_path=_isolate_sqlite,
        )

    def test_spider_stats(self, _isolate_sqlite, session_id):
        stats = {
            'phase1_discovered': 50, 'phase1_processed': 40,
            'phase1_skipped': 5, 'phase1_no_new': 3, 'phase1_failed': 2,
            'phase2_discovered': 30, 'phase2_processed': 25,
            'phase2_skipped': 3, 'phase2_no_new': 1, 'phase2_failed': 1,
            'total_discovered': 80, 'total_processed': 65,
            'total_skipped': 8, 'total_no_new': 4, 'total_failed': 3,
        }
        db_mod.db_save_spider_stats(session_id, stats)
        loaded = db_mod.db_get_spider_stats(session_id)
        assert loaded is not None
        assert loaded['phase1_discovered'] == 50
        assert loaded['total_failed'] == 3

    def test_uploader_stats(self, _isolate_sqlite, session_id):
        stats = {
            'total_torrents': 100, 'duplicate_count': 10,
            'attempted': 90, 'successfully_added': 85,
            'failed_count': 5, 'hacked_sub': 20,
            'hacked_nosub': 15, 'subtitle_count': 30,
            'no_subtitle_count': 25, 'success_rate': 94.4,
        }
        db_mod.db_save_uploader_stats(session_id, stats)
        loaded = db_mod.db_get_uploader_stats(session_id)
        assert loaded is not None
        assert loaded['total_torrents'] == 100
        assert loaded['success_rate'] == pytest.approx(94.4, rel=0.01)

    def test_pikpak_stats(self, _isolate_sqlite, session_id):
        stats = {
            'threshold_days': 3, 'total_torrents': 50,
            'filtered_old': 20, 'successful_count': 15, 'failed_count': 2,
            'uploaded_count': 18, 'delete_failed_count': 3,
        }
        db_mod.db_save_pikpak_stats(session_id, stats)
        loaded = db_mod.db_get_pikpak_stats(session_id)
        assert loaded is not None
        assert loaded['successful_count'] == 15
        assert loaded['uploaded_count'] == 18
        assert loaded['delete_failed_count'] == 3

    def test_stats_missing_session(self, _isolate_sqlite):
        assert db_mod.db_get_spider_stats(9999) is None
        assert db_mod.db_get_uploader_stats(9999) is None
        assert db_mod.db_get_pikpak_stats(9999) is None


# ── session_id extraction (logic from pipeline.py) ────────────────────────

class TestSessionIdExtraction:
    @staticmethod
    def _extract(output):
        """Mirrors pipeline.extract_session_id_from_output without importing pipeline."""
        for line in output.splitlines():
            if line.startswith('SPIDER_SESSION_ID='):
                try:
                    return int(line.split('=', 1)[1].strip())
                except (ValueError, IndexError):
                    return None
        return None

    def test_extract_valid(self):
        assert self._extract("log line\nSPIDER_SESSION_ID=42\nmore") == 42

    def test_extract_missing(self):
        assert self._extract("no id here\nfoo=bar") is None

    def test_extract_invalid(self):
        assert self._extract("SPIDER_SESSION_ID=abc") is None

    def test_extract_empty(self):
        assert self._extract("") is None


# ── migration script ─────────────────────────────────────────────────────

class TestReportMigration:
    def test_parse_daily_filename(self):
        from migration.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename('Javdb_TodayTitle_20240101.csv', is_adhoc_dir=False)
        assert result['report_type'] == 'daily'
        assert result['report_date'] == '20240101'

    def test_parse_adhoc_filename(self):
        from migration.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename(
            'Javdb_AdHoc_actors_TestActor_20240201.csv', is_adhoc_dir=True)
        assert result['report_type'] == 'adhoc'
        assert result['url_type'] == 'actors'
        assert result['display_name'] == 'TestActor'
        assert result['report_date'] == '20240201'

    def test_parse_adhoc_rankings(self):
        from migration.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename(
            'Javdb_AdHoc_rankings_top_20241231.csv', is_adhoc_dir=True)
        assert result['url_type'] == 'rankings'
        assert result['display_name'] == 'top'

    def test_parse_today_title_in_adhoc_dir(self):
        from migration.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename('Javdb_TodayTitle_20250629.csv', is_adhoc_dir=True)
        assert result['report_type'] == 'adhoc'

    def test_migrate_single_csv(self, _isolate_sqlite, tmp_path):
        from migration.csv_to_sqlite import migrate_single_csv
        csv_path = str(tmp_path / "test_report.csv")
        with open(csv_path, 'w', encoding='utf-8-sig') as f:
            f.write('href,video_code,page,actor,rate,comment_number,'
                    'hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle,'
                    'size_hacked_subtitle,size_hacked_no_subtitle,size_subtitle,size_no_subtitle\n')
            f.write('/v/A,A-001,1,TestActor,4.5,100,mag1,,mag2,,1GB,,2GB,\n')
            f.write('/v/B,B-002,1,,,,,,mag3,,,,,\n')

        result = migrate_single_csv(
            csv_path, 'Javdb_TodayTitle_20240101.csv',
            is_adhoc=False, db_path=_isolate_sqlite, dry_run=False)
        assert result['session_id'] is not None
        assert result['row_count'] == 2

        rows = db_mod.db_get_report_rows(result['session_id'], db_path=_isolate_sqlite)
        assert len(rows) == 2
        assert rows[0]['video_code'] == 'A-001'

    def test_skip_already_migrated(self, _isolate_sqlite, tmp_path):
        from migration.csv_to_sqlite import migrate_single_csv
        csv_path = str(tmp_path / "dup.csv")
        with open(csv_path, 'w', encoding='utf-8-sig') as f:
            f.write('href,video_code,page,actor,rate,comment_number,'
                    'hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle,'
                    'size_hacked_subtitle,size_hacked_no_subtitle,size_subtitle,size_no_subtitle\n')
            f.write('/v/A,A,1,,,,,,,,,,,\n')

        migrate_single_csv(csv_path, 'dup.csv', False, _isolate_sqlite, False)
        result2 = migrate_single_csv(csv_path, 'dup.csv', False, _isolate_sqlite, False)
        assert result2['skipped'] is True
