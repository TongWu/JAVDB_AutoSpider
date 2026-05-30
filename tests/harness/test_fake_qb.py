from tests.harness.fake_qb import FakeQB

_MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def test_add_then_listed_as_downloading():
    qb = FakeQB()
    assert qb.add_torrent(_MAGNET, category="JavDB") is True
    rows = qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="downloading")
    assert len(rows) == 1
    assert rows[0]["hash"] == "a" * 40
    assert rows[0]["state"] == "downloading"


def test_existing_hashes_reflects_adds():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    assert "a" * 40 in qb.get_existing_hashes()


def test_complete_moves_to_completed_filter():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    qb.complete("a" * 40)
    assert qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="downloading") == []
    done = qb.get_torrents_multiple_categories(["JavDB"], torrent_filter="completed")
    assert done[0]["progress"] == 1.0


def test_delete_removes():
    qb = FakeQB()
    qb.add_torrent(_MAGNET, category="JavDB")
    qb.delete_torrents(["a" * 40])
    assert qb.all_hashes() == set()
