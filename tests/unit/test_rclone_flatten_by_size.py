"""Unit tests for ``apps.cli.rclone.flatten_by_size`` name collision handling.

The regression guarded here: a nested file promoted to the operated root must
never resolve to the exact path of a file that already lives at that root.
``rclone moveto`` has no ``--ignore-existing`` in ``execute_moveto`` and
overwrites a differing destination by default, so a colliding destination name
silently destroys the root file.
"""

from apps.cli.rclone import flatten_by_size as flatten


def _row(rel_path: str, size: int = 1) -> flatten.FileRow:
    return flatten.FileRow(rel_path=rel_path, size=size)


def test_single_nested_peer_colliding_with_root_file_gets_slug():
    """One nested file + a same-named root file must not target the root file."""
    nested = [_row("无码流出-中字/ABC-123.mp4")]
    at_root = [_row("ABC-123.mp4")]

    dst = flatten.choose_dst_names(nested, at_root)

    assert dst["无码流出-中字/ABC-123.mp4"] != "ABC-123.mp4"
    # Same slug scheme as a multi-nested-peer collision: "<dir slug>__<base>"
    assert dst["无码流出-中字/ABC-123.mp4"] == "无码流出-中字__ABC-123.mp4"


def test_multiple_nested_peers_still_use_dir_slug():
    nested = [_row("a/ABC-123.mp4"), _row("b/ABC-123.mp4")]

    dst = flatten.choose_dst_names(nested)

    assert dst == {
        "a/ABC-123.mp4": "a__ABC-123.mp4",
        "b/ABC-123.mp4": "b__ABC-123.mp4",
    }


def test_non_colliding_nested_file_keeps_bare_basename():
    nested = [_row("a/XYZ-001.mp4")]
    at_root = [_row("ABC-123.mp4")]

    dst = flatten.choose_dst_names(nested, at_root)

    assert dst == {"a/XYZ-001.mp4": "XYZ-001.mp4"}


def test_slug_candidate_colliding_with_root_file_is_uniquified():
    """The generated slug name must also be checked against root basenames."""
    nested = [_row("a/ABC-123.mp4")]
    at_root = [_row("ABC-123.mp4"), _row("a__ABC-123.mp4")]

    dst = flatten.choose_dst_names(nested, at_root)

    assert dst["a/ABC-123.mp4"] not in {"ABC-123.mp4", "a__ABC-123.mp4"}
    assert dst["a/ABC-123.mp4"] == "a__ABC-123_2.mp4"


def test_root_files_default_to_empty_when_omitted():
    """Signature stays backwards compatible: no root files == old behaviour."""
    nested = [_row("a/ABC-123.mp4")]

    assert flatten.choose_dst_names(nested) == {"a/ABC-123.mp4": "ABC-123.mp4"}
