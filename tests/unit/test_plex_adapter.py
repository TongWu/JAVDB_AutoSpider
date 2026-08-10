from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.integrations.media_servers.plex.adapter import PlexAdapter, _watched_at_iso


class _FakeHttp:
    def __init__(self, sections_json, items_json):
        self._sections_json = sections_json
        self._items_json = items_json

    def get_json(self, path, params=None):
        # Plex hits exactly two endpoints: '/library/sections' (discovery) and
        # '/library/sections/{key}/all' (items). Route explicitly on the sections
        # endpoint rather than a fragile path-depth heuristic.
        if path.rstrip("/").endswith("/sections"):
            return self._sections_json
        return self._items_json


def test_plex_lists_items_with_signal():
    cfg = MediaServerConfig("plex", "plex-home", "http://h:32400", "T", ("JAV",))
    http = _FakeHttp(
        sections_json={"MediaContainer": {"Directory": [
            {"key": "3", "title": "JAV", "type": "movie"},
        ]}},
        items_json={"MediaContainer": {"Metadata": [{
            "ratingKey": "998", "title": "STARS-789",
            "viewCount": 1, "viewOffset": 0, "duration": 1000,
            "userRating": 8.0,
            "Media": [{"Part": [{"file": "/m/STARS-789/STARS-789.mkv"}]}],
        }]}},
    )
    items = PlexAdapter(cfg, http=http).list_items(since=None)
    assert len(items) == 1
    it = items[0]
    assert it.instance == "plex-home"
    assert it.source_type == "plex"
    assert it.library_id == "3"
    assert it.item_id == "998"
    assert it.file_path == "/m/STARS-789/STARS-789.mkv"
    assert it.watched is True       # viewCount >= 1
    assert it.rating == 8.0
    assert it.library_name == "JAV"           # section title → library_name
    assert it.play_count == 1                 # viewCount passed through
    assert it.progress_pct == 0              # viewOffset=0 / duration=1000 → 0%


def test_plex_last_viewed_at_epoch_becomes_iso():
    """Regression: the raw Plex epoch used to be stored verbatim, so the
    consumption trend (substr(watched_at,1,10) + >= 'YYYY-MM-DD' cutoff) either
    dropped the row or grouped it under an invalid day key."""
    cfg = MediaServerConfig("plex", "plex-home", "http://h:32400", "T", ())
    http = _FakeHttp(
        sections_json={"MediaContainer": {"Directory": [{"key": "3", "title": "JAV"}]}},
        items_json={"MediaContainer": {"Metadata": [{
            "ratingKey": "998", "title": "STARS-789",
            "viewCount": 1, "viewOffset": 500, "duration": 1000,
            "lastViewedAt": 1712345678,
        }]}},
    )
    items = PlexAdapter(cfg, http=http).list_items(since=None)
    assert items[0].watched_at == "2024-04-05T19:34:38Z"


def test_watched_at_iso_handles_epoch_string_and_passthrough():
    # Plex sends the epoch as a number, but a JSON string of digits is equivalent.
    assert _watched_at_iso("1712345678") == "2024-04-05T19:34:38Z"
    # An ISO value (what Emby produces) is already the target shape — leave it.
    assert _watched_at_iso("2026-06-01T20:00:00Z") == "2026-06-01T20:00:00Z"
    # Missing / epoch-0 means "never viewed", not 1970. The string forms matter:
    # '0' is a truthy string, so the falsy guard alone let it through as
    # 1970-01-01 — a real-looking day in the consumption trend.
    assert _watched_at_iso(None) is None
    assert _watched_at_iso("") is None
    assert _watched_at_iso(0) is None
    assert _watched_at_iso("0") is None
    assert _watched_at_iso("0.0") is None
    # Negative epochs are equally meaningless here (they became 1969 dates).
    assert _watched_at_iso("-1") is None
    # Out-of-range values must be dropped, not raised.
    assert _watched_at_iso("inf") is None
