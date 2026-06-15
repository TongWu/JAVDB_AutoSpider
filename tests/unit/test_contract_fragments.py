"""ADR-055: the fragment registry is well-formed and queryable."""
from javdb.storage.contract import fragments
from javdb.storage.contract.types import SqlFragment, normalize_sql


def test_registry_is_nonempty_tuple_of_fragments():
    assert fragments.FRAGMENTS
    assert all(isinstance(f, SqlFragment) for f in fragments.FRAGMENTS)


def test_fragment_names_unique():
    names = [f.name for f in fragments.FRAGMENTS]
    assert len(names) == len(set(names))


def test_param_count_matches_placeholders():
    for f in fragments.FRAGMENTS:
        assert normalize_sql(f.sql).count("?") == len(f.params), f.name


def test_watch_intent_upsert_shape():
    f = fragments.WATCH_INTENT_UPSERT
    assert f.name == "watch_intent_upsert"
    assert f.db == "history"
    assert [p.name for p in f.params] == ["video_code", "href", "status", "notes"]
    norm = normalize_sql(f.sql)
    assert norm.startswith("INSERT INTO WatchIntent")
    assert "ON CONFLICT(video_code) DO UPDATE SET" in norm
    assert "notes = COALESCE(excluded.notes, notes)" in norm
