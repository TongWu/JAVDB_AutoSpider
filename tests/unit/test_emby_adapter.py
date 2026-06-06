from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.integrations.media_servers.emby.adapter import EmbyAdapter


class _FakeHttp:
    """Returns canned JSON per (url, params) the adapter requests."""
    def __init__(self, library_json, items_json):
        self._library_json = library_json
        self._items_json = items_json

    def get_json(self, path, params=None):
        if "Views" in path or "Library" in path:
            return self._library_json
        return self._items_json


def test_emby_lists_items_with_signal():
    cfg = MediaServerConfig("emby", "emby-nas", "http://nas:8096", "TOKEN", ("JAV",))
    http = _FakeHttp(
        library_json={"Items": [{"Id": "lib7", "Name": "JAV"}]},
        items_json={"Items": [{
            "Id": "42", "Name": "ABC-123 Title",
            "Path": "/media/ABC-123/ABC-123.mp4",
            "UserData": {"Played": True, "PlayCount": 2, "PlayedPercentage": 100},
        }]},
    )
    items = EmbyAdapter(cfg, http=http).list_items(since=None)
    assert len(items) == 1
    it = items[0]
    assert it.instance == "emby-nas"
    assert it.source_type == "emby"
    assert it.library_id == "lib7"
    assert it.library_name == "JAV"
    assert it.item_id == "42"
    assert it.file_path == "/media/ABC-123/ABC-123.mp4"
    assert it.watched is True
    assert it.play_count == 2
    assert it.progress_pct == 100
