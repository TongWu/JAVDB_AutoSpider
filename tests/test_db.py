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
            'DedupRecords', 'MovieHistory', 'TorrentHistory', 'PikpakHistory',
            'PikpakStats', 'ProxyBans', 'RcloneInventory',
            'ReportMovies', 'ReportTorrents', 'ReportSessions', 'SchemaVersion',
            'SpiderStats', 'UploaderStats',
        }
        assert expected.issubset(set(tables))

    def test_wal_mode(self, _isolate_sqlite):
        with db_mod.get_db(_isolate_sqlite) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == 'wal'

    def test_idempotent(self, _isolate_sqlite):
        db_mod.init_db(_isolate_sqlite)
        db_mod.init_db(_isolate_sqlite)

    def test_moviehistory_actor_column_order_normalize(self, tmp_path):
        """Legacy ALTER order Name→Link→Gender is rebuilt as Name→Gender→Link→Supporting."""
        p = str(tmp_path / "order_test.db")
        conn = sqlite3.connect(p)
        conn.executescript(
            """
            CREATE TABLE MovieHistory (
              Id INTEGER PRIMARY KEY AUTOINCREMENT,
              VideoCode TEXT NOT NULL,
              Href TEXT NOT NULL UNIQUE,
              DateTimeCreated TEXT,
              DateTimeUpdated TEXT,
              DateTimeVisited TEXT,
              PerfectMatchIndicator INTEGER DEFAULT 0,
              HiResIndicator INTEGER DEFAULT 0
            );
            ALTER TABLE MovieHistory ADD COLUMN ActorName TEXT DEFAULT '';
            ALTER TABLE MovieHistory ADD COLUMN ActorLink TEXT DEFAULT '';
            ALTER TABLE MovieHistory ADD COLUMN ActorGender TEXT DEFAULT '';
            ALTER TABLE MovieHistory ADD COLUMN SupportingActors TEXT DEFAULT '';
            """
        )
        conn.commit()
        conn.close()

        c2 = sqlite3.connect(p)
        try:
            assert not db_mod.moviehistory_actor_layout_ok(c2)
        finally:
            c2.close()

        with db_mod.get_db(p) as gconn:
            db_mod._normalize_moviehistory_actor_column_order(gconn)

        c3 = sqlite3.connect(p)
        try:
            assert db_mod.moviehistory_actor_layout_ok(c3)
            names = [r[1] for r in c3.execute("PRAGMA table_info(MovieHistory)").fetchall()]
            i_n = names.index("ActorName")
            i_g = names.index("ActorGender")
            i_l = names.index("ActorLink")
            i_s = names.index("SupportingActors")
            assert i_n < i_g < i_l < i_s
        finally:
            c3.close()

    def test_init_db_noop_in_csv_mode(self, tmp_path, storage_mode_csv):
        """init_db should be a no-op when STORAGE_MODE='csv'."""
        fresh_db = str(tmp_path / "should_not_exist.db")
        db_mod.init_db(fresh_db)
        assert not os.path.exists(fresh_db)

    def test_split_db_init(self, tmp_path):
        """init_db() without db_path should create three separate DB files."""
        import utils.config_helper as _cfg_mod
        orig_override = _cfg_mod._storage_mode_override
        _cfg_mod._storage_mode_override = 'db'

        orig_db = db_mod.DB_PATH
        orig_h = db_mod.HISTORY_DB_PATH
        orig_r = db_mod.REPORTS_DB_PATH
        orig_o = db_mod.OPERATIONS_DB_PATH

        db_mod.DB_PATH = str(tmp_path / 'old.db')
        db_mod.HISTORY_DB_PATH = str(tmp_path / 'history.db')
        db_mod.REPORTS_DB_PATH = str(tmp_path / 'reports.db')
        db_mod.OPERATIONS_DB_PATH = str(tmp_path / 'operations.db')

        try:
            db_mod.init_db(force=True)

            assert os.path.exists(db_mod.HISTORY_DB_PATH)
            assert os.path.exists(db_mod.REPORTS_DB_PATH)
            assert os.path.exists(db_mod.OPERATIONS_DB_PATH)

            def _tables(path):
                conn = sqlite3.connect(path)
                t = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                conn.close()
                return t

            h_tables = _tables(db_mod.HISTORY_DB_PATH)
            assert 'MovieHistory' in h_tables
            assert 'TorrentHistory' in h_tables
            assert 'ReportSessions' not in h_tables

            r_tables = _tables(db_mod.REPORTS_DB_PATH)
            assert 'ReportSessions' in r_tables
            assert 'SpiderStats' in r_tables
            assert 'MovieHistory' not in r_tables

            o_tables = _tables(db_mod.OPERATIONS_DB_PATH)
            assert 'RcloneInventory' in o_tables
            assert 'DedupRecords' in o_tables
            assert 'MovieHistory' not in o_tables
        finally:
            db_mod.close_db()
            db_mod.DB_PATH = orig_db
            db_mod.HISTORY_DB_PATH = orig_h
            db_mod.REPORTS_DB_PATH = orig_r
            db_mod.OPERATIONS_DB_PATH = orig_o
            _cfg_mod._storage_mode_override = orig_override

    def test_split_migration_from_single_db(self, tmp_path):
        """Placing a v6 single DB at DB_PATH triggers automatic split."""
        import utils.config_helper as _cfg_mod
        orig_override = _cfg_mod._storage_mode_override
        _cfg_mod._storage_mode_override = 'db'

        orig_db = db_mod.DB_PATH
        orig_h = db_mod.HISTORY_DB_PATH
        orig_r = db_mod.REPORTS_DB_PATH
        orig_o = db_mod.OPERATIONS_DB_PATH

        single_db = str(tmp_path / 'javdb_autospider.db')
        db_mod.DB_PATH = single_db
        db_mod.HISTORY_DB_PATH = str(tmp_path / 'history.db')
        db_mod.REPORTS_DB_PATH = str(tmp_path / 'reports.db')
        db_mod.OPERATIONS_DB_PATH = str(tmp_path / 'operations.db')

        try:
            # Create v6 single DB with some data
            db_mod.init_db(single_db, force=True)
            db_mod.db_upsert_history('/v/T1', 'T1',
                                     magnet_links={'subtitle': 'magnet:?xt=urn:btih:test1'},
                                     db_path=single_db)
            sid = db_mod.db_create_report_session(
                'daily', '20240101', 'test.csv', db_path=single_db)
            db_mod.db_append_dedup_record(
                {'video_code': 'T1', 'existing_gdrive_path': 'p'},
                db_path=single_db)
            db_mod.close_db()

            # Now init_db() without db_path should detect + split
            db_mod.init_db(force=True)

            assert os.path.exists(db_mod.HISTORY_DB_PATH)
            assert os.path.exists(db_mod.REPORTS_DB_PATH)
            assert os.path.exists(db_mod.OPERATIONS_DB_PATH)
            assert not os.path.exists(single_db)
            assert os.path.exists(single_db + '.v6.bak')

            # Verify data made it into the correct DBs
            history = db_mod.db_load_history()
            assert '/v/T1' in history

            latest = db_mod.db_get_latest_session()
            assert latest is not None

            dedup = db_mod.db_load_dedup_records()
            assert len(dedup) >= 1
        finally:
            db_mod.close_db()
            db_mod.DB_PATH = orig_db
            db_mod.HISTORY_DB_PATH = orig_h
            db_mod.REPORTS_DB_PATH = orig_r
            db_mod.OPERATIONS_DB_PATH = orig_o
            _cfg_mod._storage_mode_override = orig_override


# ── parsed_movies_history ─────────────────────────────────────────────────

class TestHistory:
    def _upsert(self, href='/v/ABC-123', code='ABC-123', magnets=None,
                actor_name=None, actor_gender=None, actor_link=None,
                supporting_actors=None):
        db_mod.db_upsert_history(
            href, code, magnet_links=magnets,
            actor_name=actor_name, actor_gender=actor_gender,
            actor_link=actor_link, supporting_actors=supporting_actors,
        )

    def test_upsert_and_load(self, _isolate_sqlite):
        self._upsert()
        history = db_mod.db_load_history()
        assert '/v/ABC-123' in history
        assert history['/v/ABC-123']['VideoCode'] == 'ABC-123'

    def test_upsert_updates_existing(self, _isolate_sqlite):
        self._upsert()
        self._upsert(magnets={'subtitle': 'magnet:new'})
        history = db_mod.db_load_history()
        assert len(history) == 1
        assert any('magnet:new' in t.get('MagnetUri', '') for t in history['/v/ABC-123'].get('torrents', {}).values())

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
        assert history['/v/A']['DateTimeVisited'] != ''

    def test_upsert_sets_actor_columns(self, _isolate_sqlite):
        self._upsert(
            actor_name='Actor One', actor_gender='female', actor_link='/actors/xyz',
            supporting_actors='[]',
        )
        history = db_mod.db_load_history()
        assert history['/v/ABC-123']['ActorName'] == 'Actor One'
        assert history['/v/ABC-123']['ActorGender'] == 'female'
        assert history['/v/ABC-123']['ActorLink'] == '/actors/xyz'
        assert history['/v/ABC-123']['SupportingActors'] == '[]'

    def test_batch_update_movie_actors(self, _isolate_sqlite):
        self._upsert(href='/v/A', code='A')
        assert db_mod.db_batch_update_movie_actors([
            ('/v/A', 'N1', 'male', '/actors/1', '[{"name":"X","gender":"","link":"/actors/x"}]'),
        ]) == 1
        history = db_mod.db_load_history()
        assert history['/v/A']['ActorName'] == 'N1'
        assert history['/v/A']['ActorGender'] == 'male'
        assert history['/v/A']['ActorLink'] == '/actors/1'
        assert 'X' in history['/v/A']['SupportingActors']

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
        assert rows[0]['VideoCode'] == 'DUP-001'

    def test_save_overwrites(self, _isolate_sqlite):
        db_mod.db_append_dedup_record(self._rec('A'))
        db_mod.db_append_dedup_record(self._rec('B'))
        rows = db_mod.db_load_dedup_records()
        assert len(rows) == 2

        rows[0]['IsDeleted'] = 1
        rows[0]['DateTimeDeleted'] = '2024-06-01'
        db_mod.db_save_dedup_records(rows)

        reloaded = db_mod.db_load_dedup_records()
        assert len(reloaded) == 2
        deleted = [r for r in reloaded if r.get('IsDeleted') in (1, True)]
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
        a_row = [r for r in rows if r['VideoCode'] == 'A'][0]
        b_row = [r for r in rows if r['VideoCode'] == 'B'][0]
        assert a_row['IsDeleted'] == 1
        assert a_row['DateTimeDeleted'] == '2024-06-01 10:00:00'
        assert b_row['IsDeleted'] == 0

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
        codes = {r['VideoCode'] for r in rows}
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
            rows = conn.execute("SELECT * FROM PikpakHistory").fetchall()
        assert len(rows) == 1
        assert dict(rows[0])['TorrentHash'] == 'abc123'


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
        names = {r['ProxyName'] for r in loaded}
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
        assert loaded[0]['ProxyName'] == 'new'


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
        assert latest['Id'] == sid2

        latest_daily = db_mod.db_get_latest_session(report_type='daily', db_path=_isolate_sqlite)
        assert latest_daily['CsvFilename'] == 'first.csv'

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
        assert loaded['Phase1Discovered'] == 50
        assert loaded['TotalFailed'] == 3

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
        assert loaded['TotalTorrents'] == 100
        assert loaded['SuccessRate'] == pytest.approx(94.4, rel=0.01)

    def test_pikpak_stats(self, _isolate_sqlite, session_id):
        stats = {
            'threshold_days': 3, 'total_torrents': 50,
            'filtered_old': 20, 'successful_count': 15, 'failed_count': 2,
            'uploaded_count': 18, 'delete_failed_count': 3,
        }
        db_mod.db_save_pikpak_stats(session_id, stats)
        loaded = db_mod.db_get_pikpak_stats(session_id)
        assert loaded is not None
        assert loaded['SuccessfulCount'] == 15
        assert loaded['UploadedCount'] == 18
        assert loaded['DeleteFailedCount'] == 3

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
        from migration.tools.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename('Javdb_TodayTitle_20240101.csv', is_adhoc_dir=False)
        assert result['report_type'] == 'daily'
        assert result['report_date'] == '20240101'

    def test_parse_adhoc_filename(self):
        from migration.tools.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename(
            'Javdb_AdHoc_actors_TestActor_20240201.csv', is_adhoc_dir=True)
        assert result['report_type'] == 'adhoc'
        assert result['url_type'] == 'actors'
        assert result['display_name'] == 'TestActor'
        assert result['report_date'] == '20240201'

    def test_parse_adhoc_rankings(self):
        from migration.tools.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename(
            'Javdb_AdHoc_rankings_top_20241231.csv', is_adhoc_dir=True)
        assert result['url_type'] == 'rankings'
        assert result['display_name'] == 'top'

    def test_parse_today_title_in_adhoc_dir(self):
        from migration.tools.csv_to_sqlite import parse_csv_filename
        result = parse_csv_filename('Javdb_TodayTitle_20250629.csv', is_adhoc_dir=True)
        assert result['report_type'] == 'adhoc'

    def test_migrate_single_csv(self, _isolate_sqlite, tmp_path):
        from migration.tools.csv_to_sqlite import migrate_single_csv
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
        from migration.tools.csv_to_sqlite import migrate_single_csv
        csv_path = str(tmp_path / "dup.csv")
        with open(csv_path, 'w', encoding='utf-8-sig') as f:
            f.write('href,video_code,page,actor,rate,comment_number,'
                    'hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle,'
                    'size_hacked_subtitle,size_hacked_no_subtitle,size_subtitle,size_no_subtitle\n')
            f.write('/v/A,A,1,,,,,,,,,,,\n')

        migrate_single_csv(csv_path, 'dup.csv', False, _isolate_sqlite, False)
        result2 = migrate_single_csv(csv_path, 'dup.csv', False, _isolate_sqlite, False)
        assert result2['skipped'] is True
