import pytest


def test_rust_movie_index_entry_preserves_positional_title_and_family_keyword():
    rust_core = pytest.importorskip("javdb.rust_core")
    if not hasattr(rust_core, "RustMovieIndexEntry"):
        pytest.skip("Rust extension is not installed")
    entry_cls = rust_core.RustMovieIndexEntry

    legacy_positional = entry_cls("/v/x", "ABC-123", "Title")
    legacy_dict = legacy_positional.to_dict()
    assert legacy_dict["title"] == "Title"
    assert legacy_dict["video_code_family"] == ""
    assert legacy_positional.video_code_family == ""

    family_entry = entry_cls(
        "/v/y",
        "Wifey.2026.05.30",
        "Western Title",
        video_code_family="western_studio_date",
    )
    family_dict = family_entry.to_dict()
    assert family_dict["title"] == "Western Title"
    assert family_dict["video_code_family"] == "western_studio_date"
    assert "video_code_family" not in family_entry.to_legacy_dict()
