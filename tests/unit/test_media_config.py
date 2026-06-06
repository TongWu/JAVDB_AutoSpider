import pytest

from javdb.infra.masking import mask_full
from javdb.ops.reconcile.media_config import MediaServerConfig, parse_media_servers


def test_parse_minimal_emby():
    cfgs = parse_media_servers([
        {"type": "emby", "instance": "emby-nas",
         "base_url": "http://nas:8096", "token": "SECRET"},
    ])
    assert len(cfgs) == 1
    c = cfgs[0]
    assert c.source_type == "emby"
    assert c.instance == "emby-nas"
    assert c.base_url == "http://nas:8096"
    assert c.token == "SECRET"
    assert c.libraries == ()


def test_parse_plex_with_libraries():
    cfgs = parse_media_servers([
        {"type": "plex", "instance": "plex-home", "base_url": "http://h:32400",
         "token": "T", "libraries": ["JAV", "Movies"]},
    ])
    assert cfgs[0].libraries == ("JAV", "Movies")


def test_token_is_masked_in_repr():
    cfgs = parse_media_servers([
        {"type": "plex", "instance": "p", "base_url": "http://h", "token": "supersecret"},
    ])
    text = repr(cfgs[0])
    assert "supersecret" not in text
    assert mask_full("supersecret") in text


def test_rejects_unknown_type():
    with pytest.raises(ValueError, match="unsupported media server type"):
        parse_media_servers([{"type": "jellyfin", "instance": "j",
                              "base_url": "http://h", "token": "T"}])


def test_rejects_missing_required_field():
    with pytest.raises(ValueError, match="missing"):
        parse_media_servers([{"type": "emby", "instance": "e", "token": "T"}])  # no base_url


def test_rejects_duplicate_instance():
    with pytest.raises(ValueError, match="duplicate instance"):
        parse_media_servers([
            {"type": "emby", "instance": "dup", "base_url": "http://a", "token": "T"},
            {"type": "plex", "instance": "dup", "base_url": "http://b", "token": "T"},
        ])


def test_rejects_libraries_as_bare_string():
    with pytest.raises(ValueError, match="'libraries' must be a list of names"):
        parse_media_servers([
            {"type": "emby", "instance": "e", "base_url": "http://h", "token": "T",
             "libraries": "JAV"},
        ])


def test_empty_or_none_returns_empty_list():
    assert parse_media_servers(None) == []
    assert parse_media_servers([]) == []
