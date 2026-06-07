import json
import os

from tests.harness.cassette import load_cassette, save_cassette


def test_cassette_round_trip(tmp_path):
    pages = {
        "https://javdb.com?page=1": "<html>index</html>",
        "https://javdb.com/v/AAA111": "<html>detail A</html>",
    }
    cassette_dir = str(tmp_path / "cass")
    save_cassette(cassette_dir, pages)
    assert load_cassette(cassette_dir) == pages


def test_cassette_writes_manifest_and_bodies(tmp_path):
    cassette_dir = str(tmp_path / "cass")
    save_cassette(cassette_dir, {"https://javdb.com/v/AAA111": "<html>a</html>"})
    assert os.path.exists(os.path.join(cassette_dir, "manifest.json"))
    assert os.path.exists(os.path.join(cassette_dir, "0001.html"))


def test_cassette_manifest_is_stably_sorted(tmp_path):
    # The URL -> NNNN.html mapping must be a stable lexicographic sort (insertion
    # order must not leak in) so a committed golden cassette diffs legibly.
    cassette_dir = str(tmp_path / "cass")
    save_cassette(
        cassette_dir,
        {
            "https://javdb.com/v/BBB222": "<html>b</html>",
            "https://javdb.com/v/AAA111": "<html>a</html>",
        },
    )
    with open(os.path.join(cassette_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["pages"] == [
        {"url": "https://javdb.com/v/AAA111", "file": "0001.html"},
        {"url": "https://javdb.com/v/BBB222", "file": "0002.html"},
    ]
