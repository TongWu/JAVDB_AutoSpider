"""Tests for the ISO-like TEXT session id generator (db._generate_session_id)."""

import os
import re
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import javdb.storage.db.db as db_mod


_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{4}-[0-9a-f]{4}$")


class TestSessionIdFormat:
    def test_returns_str(self):
        sid = db_mod._generate_session_id()
        assert isinstance(sid, str)

    def test_matches_canonical_pattern(self):
        sid = db_mod._generate_session_id()
        assert _PATTERN.match(sid), f"unexpected shape: {sid!r}"

    def test_fixed_length(self):
        sid = db_mod._generate_session_id()
        assert len(sid) == 33, f"expected 33 chars, got {len(sid)} ({sid!r})"

    def test_module_regex_matches(self):
        sid = db_mod._generate_session_id()
        assert db_mod._SESSION_ID_PATTERN.match(sid)


class TestSessionIdMonotonicity:
    def test_successive_calls_strictly_increasing(self):
        ids = [db_mod._generate_session_id() for _ in range(10_000)]
        for prev, cur in zip(ids, ids[1:]):
            assert cur > prev, f"non-monotonic: {prev!r} >= {cur!r}"

    def test_no_duplicates_in_burst(self):
        ids = {db_mod._generate_session_id() for _ in range(10_000)}
        assert len(ids) == 10_000


class TestSessionIdProcessTag:
    def test_tag_stable_within_process(self):
        ids = [db_mod._generate_session_id() for _ in range(20)]
        tags = {sid.split("-")[1] for sid in ids}
        assert len(tags) == 1, f"process tag should be stable, got {tags}"

    def test_tag_is_four_lowercase_hex(self):
        sid = db_mod._generate_session_id()
        tag = sid.split("-")[1]
        assert re.match(r"^[0-9a-f]{4}$", tag)


class TestSessionIdCounter:
    def test_counter_increments_within_same_microsecond(self, monkeypatch):
        # Pin the clock so three back-to-back calls all observe the same µs;
        # counter must increment 0 → 1 → 2 while the timestamp prefix stays
        # identical.
        fixed_ns = 1_715_500_000_123_456_000  # arbitrary fixed nanosecond
        monkeypatch.setattr(db_mod.time, "time_ns", lambda: fixed_ns)
        # Reset module state so this test isn't influenced by prior calls.
        monkeypatch.setattr(db_mod, "_SESSION_ID_LAST", "")
        monkeypatch.setattr(db_mod, "_SESSION_ID_LAST_US", -1)
        monkeypatch.setattr(db_mod, "_SESSION_ID_COUNTER", 0)
        a = db_mod._generate_session_id()
        b = db_mod._generate_session_id()
        c = db_mod._generate_session_id()
        a_prefix, a_seq = a.rsplit("-", 1)
        b_prefix, b_seq = b.rsplit("-", 1)
        c_prefix, c_seq = c.rsplit("-", 1)
        assert a_prefix == b_prefix == c_prefix
        assert int(a_seq, 16) == 0
        assert int(b_seq, 16) == 1
        assert int(c_seq, 16) == 2

    def test_counter_resets_on_microsecond_advance(self, monkeypatch):
        clock = {"ns": 1_715_500_000_123_456_000}
        monkeypatch.setattr(db_mod.time, "time_ns", lambda: clock["ns"])
        monkeypatch.setattr(db_mod, "_SESSION_ID_LAST", "")
        monkeypatch.setattr(db_mod, "_SESSION_ID_LAST_US", -1)
        monkeypatch.setattr(db_mod, "_SESSION_ID_COUNTER", 0)
        a = db_mod._generate_session_id()
        clock["ns"] += 1_000  # advance one full µs
        b = db_mod._generate_session_id()
        a_prefix, a_seq = a.rsplit("-", 1)
        b_prefix, b_seq = b.rsplit("-", 1)
        assert a_prefix != b_prefix
        assert int(a_seq, 16) == 0
        assert int(b_seq, 16) == 0


class TestSessionIdSortability:
    def test_lexicographic_sort_equals_generation_order(self):
        ids = [db_mod._generate_session_id() for _ in range(500)]
        assert sorted(ids) == ids
