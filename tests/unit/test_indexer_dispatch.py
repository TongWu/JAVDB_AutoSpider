"""Fan-out + isolation tests for the indexer dispatcher (ADR-054 WS3)."""

import threading
import time

import javdb.integrations.indexer.dispatch as dispatch
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import PluginRegistry


class _Plugin:
    def __init__(self, name, configured=True, raises=False, magnets=None, delay=0):
        self.name = name
        self._configured = configured
        self._raises = raises
        self._magnets = magnets or []
        self._delay = delay

    def is_configured(self):
        return self._configured

    def search(self, video_code):
        if self._delay:
            time.sleep(self._delay)
        if self._raises:
            raise RuntimeError("boom")
        return IndexerResult(source=self.name, ok=True, magnets=self._magnets)


def _registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("indexer", p)
    return reg


def _mag(name, source):
    return IndexerMagnet(magnet_uri=f"magnet:?xt=urn:btih:{name}", name=name, source=source)


def test_active_sources_default_empty(monkeypatch):
    # Default empty list = feature OFF; no implicit fallback.
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: default)
    assert dispatch.active_sources() == []


def test_active_sources_csv_string(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: "javbus, sukebei")
    assert dispatch.active_sources() == ["javbus", "sukebei"]


def test_aggregate_fans_out_and_isolates_failure(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["javbus", "sukebei"])
    monkeypatch.setattr(
        dispatch,
        "REGISTRY",
        _registry(
            _Plugin("javbus", raises=True),
            _Plugin("sukebei", magnets=[_mag("ABC", "sukebei")]),
        ),
    )
    results = {r.source: r for r in dispatch.aggregate("ABC-001")}
    assert results["javbus"].ok is False
    assert results["sukebei"].ok is True
    assert results["sukebei"].magnets[0].source == "sukebei"


def test_aggregate_skips_unconfigured(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["javbus"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_Plugin("javbus", configured=False)))
    results = dispatch.aggregate("ABC-001")
    assert results[0].ok is False
    assert "not configured" in (results[0].detail or "")


def test_aggregate_reports_not_registered(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["ghost"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry())
    results = dispatch.aggregate("ABC-001")
    assert results[0].ok is False
    assert "not registered" in (results[0].detail or "")


def test_active_sources_sanitizes_invalid_list_items(monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "cfg",
        lambda name, default: ["javbus", {}, "", 123, " sukebei "],
    )
    assert dispatch.active_sources() == ["javbus", "sukebei"]


def test_aggregate_times_out_slow_source_without_blocking_fast_source(monkeypatch):
    def _cfg(name, default):
        if name == "MAGNET_SOURCES":
            return ["slow", "fast"]
        if name == "MAGNET_SOURCE_TIMEOUT_SECONDS":
            return "0.05"
        return default

    monkeypatch.setattr(dispatch, "cfg", _cfg)
    monkeypatch.setattr(
        dispatch,
        "REGISTRY",
        _registry(
            _Plugin("slow", delay=0.3),
            _Plugin("fast", magnets=[_mag("ABC", "fast")]),
        ),
    )

    results = {r.source: r for r in dispatch.aggregate("ABC-001")}

    assert results["fast"].ok is True
    assert results["fast"].magnets[0].source == "fast"
    assert results["slow"].ok is False
    assert "timeout" in (results["slow"].detail or "").lower()


class _HangPlugin:
    """A source whose search blocks until released — simulates hung network I/O."""

    def __init__(self, name, release):
        self.name = name
        self._release = release

    def is_configured(self):
        return True

    def search(self, video_code):
        self._release.wait(timeout=5)  # safety cap so a forgotten release can't wedge the suite
        return IndexerResult(source=self.name, ok=True, magnets=[])


def test_aggregate_bounds_threads_when_source_hangs(monkeypatch):
    # issue #225: repeated calls against a hung source must not leak threads. The
    # per-source bounded permits cap worker threads regardless of how many times
    # we ask; once every permit is held by a still-hung search, further calls
    # fail fast ("busy") instead of spawning more daemon threads.
    release = threading.Event()

    def _cfg(name, default):
        if name == "MAGNET_SOURCES":
            return ["hang"]
        if name == "MAGNET_SOURCE_TIMEOUT_SECONDS":
            return "0.02"
        return default

    monkeypatch.setattr(dispatch, "cfg", _cfg)
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_HangPlugin("hang", release)))

    try:
        peak = 0
        for _ in range(dispatch._MAX_WORKERS_PER_SOURCE * 3):
            results = dispatch.aggregate("ABC-001")
            assert results[0].ok is False
            # A failure reason either way: the first calls time out waiting on the
            # hung worker; once every permit is taken, later calls are refused.
            detail = (results[0].detail or "").lower()
            assert "timeout" in detail or "busy" in detail
            # Scope the count to the hung source's own daemon threads — they are
            # named "indexer-<source>", isolating this from other tests.
            live = [t for t in threading.enumerate() if t.name.startswith("indexer-hang")]
            peak = max(peak, len(live))
        assert peak <= dispatch._MAX_WORKERS_PER_SOURCE
    finally:
        # Release the hung workers so they drain their permits, then drop the
        # per-source semaphore this test created so module state stays clean.
        release.set()
        dispatch._source_slots.clear()


def test_aggregate_times_out_later_source_past_its_launch_deadline(monkeypatch):
    def _cfg(name, default):
        if name == "MAGNET_SOURCES":
            return ["first", "later"]
        if name == "MAGNET_SOURCE_TIMEOUT_SECONDS":
            return "0.05"
        return default

    monkeypatch.setattr(dispatch, "cfg", _cfg)
    monkeypatch.setattr(
        dispatch,
        "REGISTRY",
        _registry(
            _Plugin("first", delay=0.2),
            _Plugin("later", delay=0.08),
        ),
    )

    results = dispatch.aggregate("ABC-001")

    assert [result.source for result in results] == ["first", "later"]
    assert [result.ok for result in results] == [False, False]
    assert all("timeout" in (result.detail or "").lower() for result in results)
