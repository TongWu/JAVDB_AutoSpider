# tests/unit/test_code_resolver.py
from javdb.ops.reconcile.code_resolver import resolve_video_code
from javdb.ops.reconcile.models import MediaItem


def _item(**kw) -> MediaItem:
    base = dict(instance="i", source_type="plex", library_id="1", item_id="x")
    base.update(kw)
    return MediaItem(**base)


def test_high_confidence_from_file_path_basename():
    code, conf = resolve_video_code(_item(file_path="/movies/ABC-123/ABC-123.mp4"))
    assert code == "ABC-123"
    assert conf == "high"


def test_high_confidence_normalizes_fullwidth_and_case():
    # NFKC folds full-width to ASCII; resolver upper-cases (matches dedup).
    # Build full-width "ssni" via codepoints so the source stays ASCII (no RUF001).
    fullwidth_ssni = "".join(chr(c) for c in (0xFF53, 0xFF53, 0xFF4E, 0xFF49))
    code, conf = resolve_video_code(_item(file_path=f"/m/{fullwidth_ssni}-001.mkv"))
    assert code == "SSNI-001"
    assert conf == "high"


def test_high_strips_quality_and_sub_suffixes():
    # 'SSNI-001-1080P' / 'SSNI-001-C' classify as multi_hyphen, but the base
    # 'SSNI-001' is the real join key — suffixes must be stripped (PR #179).
    for path in ("/m/SSNI-001-1080p.mkv", "/m/SSNI-001-C.mp4", "/m/SSNI-001-CD1.mkv"):
        code, conf = resolve_video_code(_item(file_path=path))
        assert code == "SSNI-001", path
        assert conf == "high", path


def test_real_multi_hyphen_code_is_not_over_stripped():
    # FC2-PPV-123456 is a genuine multi_hyphen code; no known suffix to strip.
    code, conf = resolve_video_code(_item(file_path="/m/FC2-PPV-123456.mp4"))
    assert code == "FC2-PPV-123456"
    assert conf == "high"


def test_dotted_western_studio_date_code_resolved():
    # Dotted codes (e.g. Wifey.2026.05.30) are supported by the canonical parser;
    # the tokenizer must keep dots so they reach classification (PR #179).
    code, conf = resolve_video_code(_item(file_path="/m/Wifey.2026.05.30.mkv"))
    assert code == "WIFEY.2026.05.30"
    assert conf == "high"


def test_medium_confidence_from_folder_name_when_path_has_no_code():
    code, conf = resolve_video_code(
        _item(file_path="/movies/disc1/title.mkv", folder_name="STARS-789 [4K]")
    )
    assert code == "STARS-789"
    assert conf == "medium"


def test_medium_confidence_from_title_family_token():
    # MIDV-001 is classic_hyphenated family → medium even when title-only.
    code, conf = resolve_video_code(_item(title="MIDV-001 Some Title Words"))
    assert code == "MIDV-001"
    assert conf == "medium"


def test_low_confidence_plausible_but_no_family():
    # 259LUXU-1234: _is_plausible_video_code → True, classify_video_code_family → ''
    # → plausible-but-non-family title token → low tier.
    code, conf = resolve_video_code(_item(title="watch 259LUXU-1234 now"))
    assert code == "259LUXU-1234"
    assert conf == "low"


def test_none_when_no_plausible_code():
    code, conf = resolve_video_code(_item(title="Family Vacation 2024", file_path="/m/clip.mp4"))
    assert code is None
    assert conf == "none"
