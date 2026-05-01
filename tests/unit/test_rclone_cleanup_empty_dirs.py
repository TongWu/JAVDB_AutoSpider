import subprocess

from scripts import rclone_cleanup_empty_dirs as cleaner


def test_find_empty_dirs_returns_children_before_parents_and_keeps_root():
    states = [
        cleaner.DirState("remote/A/B", "A/B", [], 0),
        cleaner.DirState("remote/A", "A", ["A/B"], 0),
        cleaner.DirState("remote/C", "C", [], 1),
        cleaner.DirState("remote", "", ["A", "C"], 0),
    ]

    empty_dirs = cleaner.find_empty_dirs(states)

    assert [state.rel_path for state in empty_dirs] == ["A/B", "A"]


def test_find_empty_dirs_does_not_delete_parent_when_child_scan_failed():
    states = [
        cleaner.DirState("remote/A/B", "A/B", [], 1, scan_failed=True),
        cleaner.DirState("remote/A", "A", ["A/B"], 0),
        cleaner.DirState("remote", "", ["A"], 0),
    ]

    assert cleaner.find_empty_dirs(states) == []


def test_collect_dir_tree_returns_children_first(monkeypatch):
    tree = {
        "remote": (["A"], 0),
        "remote/A": (["B"], 0),
        "remote/A/B": ([], 0),
    }

    monkeypatch.setattr(cleaner, "list_entries", lambda path: tree[path])

    states = cleaner.collect_dir_tree("remote")

    assert [state.rel_path for state in states] == ["A/B", "A", ""]


def test_execute_cleanup_skips_parent_when_child_rmdir_fails(monkeypatch):
    states = [
        cleaner.DirState("remote/A/B", "A/B", [], 0),
        cleaner.DirState("remote/A", "A", ["A/B"], 0),
    ]

    def fake_rmdir(remote_path):
        if remote_path == "remote/A/B":
            raise subprocess.CalledProcessError(1, ["rclone", "rmdir", remote_path])

    monkeypatch.setattr(cleaner, "rmdir_remote", fake_rmdir)

    assert cleaner.execute_cleanup(states, dry_run=False) == (0, 1, 1)


def test_execute_cleanup_dry_run_marks_all_empty_dirs_removed(monkeypatch):
    calls = []
    states = [
        cleaner.DirState("remote/A/B", "A/B", [], 0),
        cleaner.DirState("remote/A", "A", ["A/B"], 0),
    ]

    monkeypatch.setattr(cleaner, "rmdir_remote", lambda path: calls.append(path))

    assert cleaner.execute_cleanup(states, dry_run=True) == (2, 0, 0)
    assert calls == []
