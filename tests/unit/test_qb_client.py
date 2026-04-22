"""Unit tests for packages/python/javdb_integrations/qb_client.py."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_integrations.qb_client import (
    QBittorrentClient,
    remove_completed_torrents_keep_files,
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
