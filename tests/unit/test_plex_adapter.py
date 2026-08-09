from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.integrations.media_servers.plex.adapter import PlexAdapter


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
