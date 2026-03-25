"""
Tests for api.parsers.common URL helper functions.
"""

import os
import sys
import json

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from api.parsers.common import (  # noqa: E402
    javdb_absolute_url,
    movie_href_lookup_values,
    absolutize_supporting_actors_json,
)


class TestJavdbAbsoluteUrl:
    def test_path_to_absolute(self):
        assert javdb_absolute_url('/actors/35Mqw', 'https://javdb.com') == 'https://javdb.com/actors/35Mqw'

    def test_absolute_url_rebased_to_base(self):
        assert javdb_absolute_url('https://mirror.example/v/AbC123', 'https://javdb.com') == 'https://javdb.com/v/AbC123'

    def test_non_site_link_passthrough(self):
        magnet = 'magnet:?xt=urn:btih:abc'
        assert javdb_absolute_url(magnet, 'https://javdb.com') == magnet


class TestMovieHrefLookupValues:
    def test_lookup_values_for_path(self):
        path, absolute = movie_href_lookup_values('/v/ABC-123', 'https://javdb.com')
        assert path == '/v/ABC-123'
        assert absolute == 'https://javdb.com/v/ABC-123'

    def test_lookup_values_for_absolute(self):
        path, absolute = movie_href_lookup_values('https://mirror.example/v/DEF-456', 'https://javdb.com')
        assert path == '/v/DEF-456'
        assert absolute == 'https://javdb.com/v/DEF-456'


class TestAbsolutizeSupportingActorsJson:
    def test_absolutize_link_key(self):
        raw = '[{"name":"A","gender":"female","link":"/actors/aaa"}]'
        out = absolutize_supporting_actors_json(raw, 'https://javdb.com')
        payload = json.loads(out)
        assert payload[0]['link'] == 'https://javdb.com/actors/aaa'

    def test_absolutize_href_key(self):
        raw = '[{"name":"B","gender":"male","href":"/actors/bbb"}]'
        out = absolutize_supporting_actors_json(raw, 'https://javdb.com')
        payload = json.loads(out)
        assert payload[0]['href'] == 'https://javdb.com/actors/bbb'

    def test_absolutize_mixed_keys(self):
        raw = '[{"name":"C","link":"/actors/ccc","href":"https://mirror.example/actors/ccc"}]'
        out = absolutize_supporting_actors_json(raw, 'https://javdb.com')
        payload = json.loads(out)
        assert payload[0]['link'] == 'https://javdb.com/actors/ccc'
        assert payload[0]['href'] == 'https://javdb.com/actors/ccc'

    def test_invalid_json_returns_original(self):
        raw = '{"not":"a list"}'
        assert absolutize_supporting_actors_json(raw, 'https://javdb.com') == raw
