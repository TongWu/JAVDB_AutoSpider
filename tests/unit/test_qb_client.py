"""Unit tests for packages/python/javdb_integrations/qb_client.py."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_integrations.qb_client import (
    LOGIN_REJECTED,
    LOGIN_SUCCESS,
    LOGIN_UNREACHABLE,
    QBittorrentClient,
    remove_completed_torrents_keep_files,
    try_login_base_urls,
    try_ping_base_urls,
)


# ---------------------------------------------------------------------------
# QBittorrentClient.from_existing_session
# ---------------------------------------------------------------------------


class TestFromExistingSession:
    def test_wraps_session_without_login(self):
        mock_session = MagicMock()

        client = QBittorrentClient.from_existing_session(
            mock_session,
            base_url='https://qb.example:8080/',
            proxies={'http': 'http://proxy:3128'},
            request_timeout=30,
        )

        assert client.session is mock_session
        assert client.base_url == 'https://qb.example:8080'
        assert client.base_urls == ['https://qb.example:8080']
        assert client.proxies == {'http': 'http://proxy:3128'}
        assert client.request_timeout == 30
        # Crucially: no login call should have been made.
        mock_session.post.assert_not_called()


# ---------------------------------------------------------------------------
# QBittorrentClient.get_torrents / get_torrents_multiple_categories
# ---------------------------------------------------------------------------


def _make_client(resp_sequence_get=None, resp_sequence_post=None):
    """Build a QBittorrentClient wrapped around a fully-mocked session."""
    session = MagicMock()
    if resp_sequence_get is not None:
        session.get.side_effect = resp_sequence_get
    if resp_sequence_post is not None:
        session.post.side_effect = resp_sequence_post
    return QBittorrentClient.from_existing_session(
        session, base_url='https://qb.example:8080'
    ), session


class TestGetTorrents:
    def test_passes_category_and_filter(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{'hash': 'h1'}]
        resp.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_get=[resp])
        result = client.get_torrents(category='Ad Hoc', torrent_filter='completed')

        assert result == [{'hash': 'h1'}]
        call = session.get.call_args
        assert call.kwargs['params'] == {'category': 'Ad Hoc', 'filter': 'completed'}

    def test_omits_category_when_none(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = []
        resp.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_get=[resp])
        client.get_torrents(category=None, torrent_filter='downloading')

        assert 'category' not in session.get.call_args.kwargs['params']
        assert session.get.call_args.kwargs['params']['filter'] == 'downloading'


class TestGetTorrentsMultipleCategories:
    def test_aggregates_results(self):
        r1 = MagicMock(); r1.json.return_value = [{'hash': 'h1'}]; r1.raise_for_status = MagicMock()
        r2 = MagicMock(); r2.json.return_value = [{'hash': 'h2'}]; r2.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_get=[r1, r2])
        result = client.get_torrents_multiple_categories(
            ['Ad Hoc', 'Daily Ingestion'], torrent_filter='completed'
        )

        assert len(result) == 2
        assert session.get.call_count == 2

    def test_continues_after_category_failure(self):
        def side_effect(*args, **kwargs):
            # First call raises, second succeeds.
            if session.get.call_count == 1:
                raise RuntimeError('qB API exploded')
            resp = MagicMock()
            resp.json.return_value = [{'hash': 'ok'}]
            resp.raise_for_status = MagicMock()
            return resp

        session = MagicMock()
        session.get.side_effect = side_effect

        client = QBittorrentClient.from_existing_session(
            session, base_url='https://qb.example:8080'
        )
        result = client.get_torrents_multiple_categories(['bad', 'good'])
        assert result == [{'hash': 'ok'}]


# ---------------------------------------------------------------------------
# QBittorrentClient.delete_torrents
# ---------------------------------------------------------------------------


class TestDeleteTorrents:
    def test_sends_correct_payload_keep_files(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_post=[resp])
        ok = client.delete_torrents(['h1', 'h2'], delete_files=False)

        assert ok is True
        data = session.post.call_args.kwargs['data']
        assert data['hashes'] == 'h1|h2'
        assert data['deleteFiles'] == 'false'

    def test_sends_correct_payload_delete_files(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_post=[resp])
        client.delete_torrents(['h1'], delete_files=True)

        data = session.post.call_args.kwargs['data']
        assert data['deleteFiles'] == 'true'

    def test_empty_hashes_is_noop(self):
        client, session = _make_client()
        ok = client.delete_torrents([])
        assert ok is True
        session.post.assert_not_called()

    def test_filters_empty_strings(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        client, session = _make_client(resp_sequence_post=[resp])
        client.delete_torrents(['', 'h1', None])

        data = session.post.call_args.kwargs['data']
        assert data['hashes'] == 'h1'


# ---------------------------------------------------------------------------
# remove_completed_torrents_keep_files
# ---------------------------------------------------------------------------


class TestRemoveCompletedTorrentsKeepFiles:
    def test_deletes_completed_with_keep_files(self):
        mock_client = MagicMock()
        mock_client.get_torrents_multiple_categories.return_value = [
            {'hash': 'h1', 'name': 'done1'},
            {'hash': 'h2', 'name': 'done2'},
        ]

        result = remove_completed_torrents_keep_files(
            mock_client, ['Ad Hoc'], dry_run=False
        )

        mock_client.get_torrents_multiple_categories.assert_called_once_with(
            ['Ad Hoc'], torrent_filter='completed'
        )
        mock_client.delete_torrents.assert_called_once_with(
            ['h1', 'h2'], delete_files=False
        )
        assert result == {
            'scanned': 2,
            'deleted': 2,
            'hashes': ['h1', 'h2'],
        }

    def test_skips_when_no_completed(self):
        mock_client = MagicMock()
        mock_client.get_torrents_multiple_categories.return_value = []

        result = remove_completed_torrents_keep_files(
            mock_client, ['Ad Hoc'], dry_run=False
        )
        mock_client.delete_torrents.assert_not_called()
        assert result == {'scanned': 0, 'deleted': 0, 'hashes': []}

    def test_dry_run_does_not_delete(self):
        mock_client = MagicMock()
        mock_client.get_torrents_multiple_categories.return_value = [
            {'hash': 'h1', 'name': 'done1'},
        ]

        result = remove_completed_torrents_keep_files(
            mock_client, ['Ad Hoc'], dry_run=True
        )
        mock_client.delete_torrents.assert_not_called()
        assert result['scanned'] == 1
        assert result['deleted'] == 0
        assert result['hashes'] == ['h1']

    def test_empty_categories_short_circuits(self):
        mock_client = MagicMock()
        result = remove_completed_torrents_keep_files(
            mock_client, [], dry_run=False
        )
        mock_client.get_torrents_multiple_categories.assert_not_called()
        mock_client.delete_torrents.assert_not_called()
        assert result == {'scanned': 0, 'deleted': 0, 'hashes': []}

    def test_label_appears_in_logs(self, caplog):
        import logging
        caplog.set_level(logging.INFO)

        mock_client = MagicMock()
        mock_client.get_torrents_multiple_categories.return_value = []

        remove_completed_torrents_keep_files(
            mock_client, ['Ad Hoc'], qb_label='Primary QB'
        )

        combined = "\n".join(r.getMessage() for r in caplog.records)
        assert 'Primary QB' in combined


# ---------------------------------------------------------------------------
# QBittorrentClient.test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    def test_returns_true_on_200(self):
        resp = MagicMock(); resp.status_code = 200
        client, session = _make_client(resp_sequence_get=[resp])
        assert client.test_connection() is True
        assert session.get.call_args.args[0].endswith('/api/v2/app/version')

    def test_returns_true_on_403(self):
        resp = MagicMock(); resp.status_code = 403
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.test_connection() is True

    def test_returns_false_on_500(self):
        resp = MagicMock(); resp.status_code = 500
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.test_connection() is False

    def test_returns_false_on_network_error(self):
        import requests as _requests
        session = MagicMock()
        session.get.side_effect = _requests.RequestException("boom")
        client = QBittorrentClient.from_existing_session(
            session, base_url='https://qb.example:8080'
        )
        assert client.test_connection() is False


# ---------------------------------------------------------------------------
# QBittorrentClient.get_existing_hashes
# ---------------------------------------------------------------------------


class TestGetExistingHashes:
    def test_returns_lowercase_hashes(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {'hash': 'AAAA', 'state': 'downloading'},
            {'hash': 'BBBB', 'state': 'seeding'},
        ]
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.get_existing_hashes() == {'aaaa', 'bbbb'}

    def test_excludes_error_states_by_default(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {'hash': 'a', 'state': 'downloading'},
            {'hash': 'b', 'state': 'error'},
            {'hash': 'c', 'state': 'missingFiles'},
            {'hash': 'd', 'state': 'seeding'},
        ]
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.get_existing_hashes() == {'a', 'd'}

    def test_custom_exclude_states(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {'hash': 'a', 'state': 'pausedUP'},
            {'hash': 'b', 'state': 'seeding'},
        ]
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.get_existing_hashes(exclude_states=('pausedUP',)) == {'b'}

    def test_returns_empty_set_on_http_error(self):
        resp = MagicMock(); resp.status_code = 500
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.get_existing_hashes() == set()

    def test_returns_empty_set_on_network_error(self):
        import requests as _requests
        session = MagicMock()
        session.get.side_effect = _requests.RequestException("boom")
        client = QBittorrentClient.from_existing_session(
            session, base_url='https://qb.example:8080'
        )
        assert client.get_existing_hashes() == set()

    def test_skips_entries_with_no_hash(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {'state': 'downloading'},
            {'hash': '', 'state': 'seeding'},
            {'hash': 'real', 'state': 'downloading'},
        ]
        client, _ = _make_client(resp_sequence_get=[resp])
        assert client.get_existing_hashes() == {'real'}


# ---------------------------------------------------------------------------
# QBittorrentClient.add_torrent
# ---------------------------------------------------------------------------


class TestAddTorrent:
    def test_sends_all_defaults_and_returns_true_on_200(self):
        resp = MagicMock(); resp.status_code = 200
        client, session = _make_client(resp_sequence_post=[resp])

        ok = client.add_torrent(
            magnet_link='magnet:?xt=urn:btih:abc',
            name='TEST-001',
            category='JavDB',
            save_path='/downloads',
        )
        assert ok is True
        call = session.post.call_args
        assert call.args[0].endswith('/api/v2/torrents/add')
        data = call.kwargs['data']
        assert data['urls'] == 'magnet:?xt=urn:btih:abc'
        assert data['name'] == 'TEST-001'
        assert data['category'] == 'JavDB'
        assert data['savepath'] == '/downloads'
        # Defaults
        assert data['autoTMM'] == 'true'
        assert data['skip_checking'] == 'false'
        assert data['contentLayout'] == 'Original'
        assert data['ratioLimit'] == '-2'
        assert data['seedingTimeLimit'] == '-2'
        assert data['addPaused'] == 'false'

    def test_paused_toggle(self):
        resp = MagicMock(); resp.status_code = 200
        client, session = _make_client(resp_sequence_post=[resp])

        client.add_torrent('magnet:?xt=urn:btih:x', paused=True)
        assert session.post.call_args.kwargs['data']['addPaused'] == 'true'

    def test_skip_checking_toggle(self):
        resp = MagicMock(); resp.status_code = 200
        client, session = _make_client(resp_sequence_post=[resp])

        client.add_torrent('magnet:?xt=urn:btih:x', skip_checking=True)
        assert session.post.call_args.kwargs['data']['skip_checking'] == 'true'

    def test_omits_name_and_category_when_none(self):
        resp = MagicMock(); resp.status_code = 200
        client, session = _make_client(resp_sequence_post=[resp])

        client.add_torrent('magnet:?xt=urn:btih:x')
        data = session.post.call_args.kwargs['data']
        assert 'name' not in data
        assert 'category' not in data

    def test_returns_false_on_non_200(self):
        resp = MagicMock(); resp.status_code = 400
        client, _ = _make_client(resp_sequence_post=[resp])
        assert client.add_torrent('magnet:?xt=urn:btih:x') is False


# ---------------------------------------------------------------------------
# Module-level try_ping_base_urls
# ---------------------------------------------------------------------------


class TestTryPingBaseUrls:
    def test_returns_first_reachable_url_200(self):
        resp = MagicMock(); resp.status_code = 200
        get_fn = MagicMock(return_value=resp)

        url, err = try_ping_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            get_fn=get_fn,
        )
        assert url == 'https://qb.internal:8080'
        assert err is None
        assert get_fn.call_count == 1

    def test_403_counts_as_reachable(self):
        resp = MagicMock(); resp.status_code = 403
        get_fn = MagicMock(return_value=resp)

        url, err = try_ping_base_urls(['https://qb.internal:8080'], get_fn=get_fn)
        assert url == 'https://qb.internal:8080'
        assert err is None

    def test_falls_back_from_ssl_error_to_http(self):
        import requests as _requests
        get_fn = MagicMock(side_effect=[
            _requests.exceptions.SSLError("ssl error"),
            MagicMock(status_code=200),
        ])
        url, err = try_ping_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            get_fn=get_fn,
        )
        assert url == 'http://qb.internal:8080'
        assert err is None
        assert get_fn.call_count == 2

    def test_500_is_not_reachable_keeps_trying(self):
        get_fn = MagicMock(side_effect=[
            MagicMock(status_code=500),
            MagicMock(status_code=200),
        ])
        url, _ = try_ping_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            get_fn=get_fn,
        )
        assert url == 'http://qb.internal:8080'

    def test_all_candidates_fail_returns_none_with_error(self):
        import requests as _requests
        get_fn = MagicMock(side_effect=[
            _requests.exceptions.SSLError("e1"),
            _requests.exceptions.ConnectionError("e2"),
        ])
        url, err = try_ping_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            get_fn=get_fn,
        )
        assert url is None
        assert err is not None

    def test_empty_candidate_list(self):
        url, err = try_ping_base_urls([], get_fn=MagicMock())
        assert url is None
        assert err is None


# ---------------------------------------------------------------------------
# Module-level try_login_base_urls
# ---------------------------------------------------------------------------


class TestTryLoginBaseUrls:
    def test_success_on_first_candidate(self):
        ok = MagicMock(status_code=200, text='Ok.')
        post_fn = MagicMock(return_value=ok)

        outcome, url, err = try_login_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            'admin', 'pw', post_fn=post_fn,
        )
        assert outcome == LOGIN_SUCCESS
        assert url == 'https://qb.internal:8080'
        assert err is None
        assert post_fn.call_count == 1

    def test_falls_back_to_http_after_ssl_error(self):
        import requests as _requests
        post_fn = MagicMock(side_effect=[
            _requests.exceptions.SSLError("ssl"),
            MagicMock(status_code=200, text='Ok.'),
        ])
        outcome, url, err = try_login_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            'admin', 'pw', post_fn=post_fn,
        )
        assert outcome == LOGIN_SUCCESS
        assert url == 'http://qb.internal:8080'
        assert err is None

    def test_credential_rejection_stops_trying(self):
        bad = MagicMock(status_code=403, text='Fails.')
        post_fn = MagicMock(return_value=bad)

        outcome, url, err = try_login_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            'admin', 'wrong', post_fn=post_fn,
        )
        assert outcome == LOGIN_REJECTED
        assert url is None
        assert err is not None
        # Must not retry on alternative URLs after a 401/403.
        assert post_fn.call_count == 1

    def test_401_also_rejected(self):
        bad = MagicMock(status_code=401, text='Unauthorized')
        post_fn = MagicMock(return_value=bad)

        outcome, _, _ = try_login_base_urls(
            ['https://qb.internal:8080'], 'a', 'b', post_fn=post_fn,
        )
        assert outcome == LOGIN_REJECTED

    def test_all_candidates_unreachable(self):
        import requests as _requests
        post_fn = MagicMock(side_effect=[
            _requests.exceptions.ConnectionError("e1"),
            _requests.exceptions.ConnectionError("e2"),
        ])
        outcome, url, err = try_login_base_urls(
            ['https://qb.internal:8080', 'http://qb.internal:8080'],
            'a', 'b', post_fn=post_fn,
        )
        assert outcome == LOGIN_UNREACHABLE
        assert url is None
        assert err is not None

    def test_passes_credentials_in_post_body(self):
        ok = MagicMock(status_code=200, text='Ok.')
        post_fn = MagicMock(return_value=ok)

        try_login_base_urls(
            ['https://qb.internal:8080'],
            'alice', 's3cret',
            post_fn=post_fn,
        )
        call = post_fn.call_args
        assert call.args[0].endswith('/api/v2/auth/login')
        assert call.kwargs['data'] == {'username': 'alice', 'password': 's3cret'}


# ---------------------------------------------------------------------------
# QBittorrentClient constructor uses the shared login helper
# ---------------------------------------------------------------------------


class TestQBittorrentClientLoginFallback:
    def test_constructor_uses_shared_fallback(self):
        """Regression check: constructing a client should walk through base
        URL candidates using the same logic as the module-level
        try_login_base_urls helper."""
        import requests as _requests

        # Patch requests.Session so we can drive its post() return sequence.
        with patch(
            'packages.python.javdb_integrations.qb_client.requests.Session'
        ) as session_cls:
            mock_session = MagicMock()
            session_cls.return_value = mock_session
            mock_session.post.side_effect = [
                _requests.exceptions.SSLError("ssl"),
                MagicMock(status_code=200, text='Ok.'),
            ]

            client = QBittorrentClient(
                ['https://qb.internal:8080', 'http://qb.internal:8080'],
                'admin', 'pw',
            )
            assert client.base_url == 'http://qb.internal:8080'
            assert mock_session.post.call_count == 2

    def test_constructor_raises_when_all_fail(self):
        import requests as _requests

        with patch(
            'packages.python.javdb_integrations.qb_client.requests.Session'
        ) as session_cls:
            mock_session = MagicMock()
            session_cls.return_value = mock_session
            mock_session.post.side_effect = _requests.exceptions.ConnectionError("x")

            with pytest.raises(Exception):
                QBittorrentClient(['https://qb.internal:8080'], 'a', 'b')
