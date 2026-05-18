"""LFS-pointer detection and storage-backend bootstrap for the
``--align-inventory-history`` path in ``apps.cli.db.migration``.

These cover the recovery flow that kicks in when the three local
SQLite mirrors (``reports/{history,reports,operations}.db``) are Git-LFS
pointer files because the smudge filter never ran (e.g. on a remote
container that lacks ``git-lfs``). The expected behaviour is:

1. Detect pointer/missing files without conflating them with empty
   SQLite databases.
2. Try ``git lfs pull`` exactly once.
3. Fall back to ``STORAGE_BACKEND=d1`` when the pull cannot recover
   them — alignment must still run, writing only to D1.
"""

from __future__ import annotations

import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.migrations import migrate_to_current as m2c


LFS_POINTER_BODY = (
    b"version https://git-lfs.github.com/spec/v1\n"
    b"oid sha256:0123456789abcdef\n"
    b"size 1234\n"
)


class TestIsLfsPointer:
    def test_pointer_file_detected(self, tmp_path):
        p = tmp_path / "history.db"
        p.write_bytes(LFS_POINTER_BODY)
        assert m2c._is_lfs_pointer(str(p)) is True

    def test_real_sqlite_not_pointer(self, tmp_path):
        # The SQLite file header always starts with "SQLite format 3\0".
        p = tmp_path / "real.db"
        p.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        assert m2c._is_lfs_pointer(str(p)) is False

    def test_large_file_short_circuits(self, tmp_path):
        # A 100KB file cannot be a pointer (pointers are tiny). Even
        # if it happens to start with the magic, the size check rules
        # it out — guards against false positives on corrupted files.
        p = tmp_path / "big.db"
        p.write_bytes(LFS_POINTER_BODY + b"\x00" * 100_000)
        assert m2c._is_lfs_pointer(str(p)) is False

    def test_missing_file(self, tmp_path):
        assert m2c._is_lfs_pointer(str(tmp_path / "nope.db")) is False


class TestBootstrapStorageBackend:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        monkeypatch.delenv("STRICT_DUAL_WRITE", raising=False)

    def _make_pointers(self, tmp_path):
        paths = []
        for name in ("history.db", "reports.db", "operations.db"):
            p = tmp_path / name
            p.write_bytes(LFS_POINTER_BODY)
            paths.append(str(p))
        return paths

    def _make_valid_sqlite(self, tmp_path):
        paths = []
        for name in ("history.db", "reports.db", "operations.db"):
            p = tmp_path / name
            p.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
            paths.append(str(p))
        return paths

    def test_intact_local_picks_dual_with_strict(self, tmp_path, monkeypatch):
        paths = self._make_valid_sqlite(tmp_path)
        assert m2c._bootstrap_storage_backend_for_align(paths) == "dual"
        assert os.environ["STORAGE_BACKEND"] == "dual"
        assert os.environ["STRICT_DUAL_WRITE"] == "1"

    def test_pointers_with_failing_lfs_pull_degrade_to_d1(
        self, tmp_path, monkeypatch,
    ):
        paths = self._make_pointers(tmp_path)
        monkeypatch.setattr(m2c, "_try_lfs_pull", lambda _paths: False)
        assert m2c._bootstrap_storage_backend_for_align(paths) == "d1"
        assert os.environ["STORAGE_BACKEND"] == "d1"

    def test_pointers_with_successful_lfs_pull_promotes_to_dual(
        self, tmp_path, monkeypatch,
    ):
        paths = self._make_pointers(tmp_path)
        # Simulate a successful pull: rewrite the files in-place to look
        # like real SQLite so subsequent checks pass.
        def fake_pull(_paths):
            for p in _paths:
                with open(p, "wb") as f:
                    f.write(b"SQLite format 3\x00" + b"\x00" * 100)
            return True

        monkeypatch.setattr(m2c, "_try_lfs_pull", fake_pull)
        assert m2c._bootstrap_storage_backend_for_align(paths) == "dual"
        assert os.environ["STORAGE_BACKEND"] == "dual"
        assert os.environ["STRICT_DUAL_WRITE"] == "1"

    def test_explicit_d1_respected_even_with_pointers(
        self, tmp_path, monkeypatch,
    ):
        # Operator opt-in to d1 must not be silently rewritten just
        # because LFS files happen to be on disk in some form — that
        # would conflate "user picked d1" with "we degraded to d1".
        paths = self._make_pointers(tmp_path)
        monkeypatch.setenv("STORAGE_BACKEND", "d1")
        # _try_lfs_pull should NOT be invoked when explicit=d1 since
        # we're skipping the local mirror entirely anyway.
        called = {"n": 0}

        def must_not_call(_paths):
            called["n"] += 1
            return False

        monkeypatch.setattr(m2c, "_try_lfs_pull", must_not_call)
        assert m2c._bootstrap_storage_backend_for_align(paths) == "d1"
        assert called["n"] == 0

    def test_explicit_sqlite_with_failed_lfs_raises(
        self, tmp_path, monkeypatch,
    ):
        paths = self._make_pointers(tmp_path)
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        monkeypatch.setattr(m2c, "_try_lfs_pull", lambda _paths: False)
        with pytest.raises(RuntimeError, match="unrecoverable"):
            m2c._bootstrap_storage_backend_for_align(paths)

    def test_explicit_sqlite_with_intact_files_kept(
        self, tmp_path, monkeypatch,
    ):
        paths = self._make_valid_sqlite(tmp_path)
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        assert m2c._bootstrap_storage_backend_for_align(paths) == "sqlite"
        # No STRICT_DUAL_WRITE set in sqlite mode — that flag is only
        # meaningful when both backends are live.
        assert "STRICT_DUAL_WRITE" not in os.environ
