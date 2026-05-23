"""W4.1 — Micro-benchmark spider hot paths to locate the next Rust target.

Runs each candidate code path in a tight loop using offline fixtures
(HTML files in ``html/`` and an in-memory SQLite), so the profiling
load is reproducible and does NOT touch the network, real proxies, or
disk-backed DBs.

Each benchmark prints wall time + iterations/sec, then cProfile's
top-N functions by cumulative time. The full ``pstats`` dump is saved
to ``reports/profiling/<benchmark>.prof`` for follow-up inspection
with ``snakeviz`` or ``gprof2dot``.

Usage:
    python3 scripts/profile_hot_paths.py
    python3 scripts/profile_hot_paths.py --only parse_detail
    python3 scripts/profile_hot_paths.py --iterations 50

Design notes:

* The HTML parsers (1a, 1b) are the most likely Rust-acceleration
  candidates after the existing one — they already proxy through the
  Rust extension when available, so this benchmark also surfaces how
  much Python-side glue cost remains.
* Throttle / sleep computation (2) is the canonical CPU candidate
  flagged in the W4 plan (10x speed-up target).
* DB benchmarks (3) intentionally use an in-memory SQLite so the
  reported time is bound by Python prep cost rather than disk I/O.
* Proxy pool benchmarks (5) report both the round-robin and
  health-weighted paths separately so the random.choices overhead is
  visible.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORTS_DIR = PROJECT_ROOT / "reports" / "profiling"
HTML_DIR = PROJECT_ROOT / "html"

# Quiet the spider's logging during profiling so the timing output stays
# readable. Anything noisier than WARNING comes from setup code we don't
# care about for hot-path numbers.
import logging  # noqa: E402
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> str:
    """Read an HTML fixture from the repo's ``html/`` directory."""
    path = HTML_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"HTML fixture missing: {path}. Run from repo root."
        )
    return path.read_text(encoding="utf-8")


def _bench(
    name: str,
    iterations: int,
    fn: Callable[[], object],
    *,
    top: int = 12,
) -> Dict[str, float]:
    """Run ``fn`` ``iterations`` times under cProfile, print summary."""
    print(f"\n── {name} ── ({iterations} iterations)")
    profiler = cProfile.Profile()
    t0 = time.perf_counter()
    profiler.enable()
    for _ in range(iterations):
        fn()
    profiler.disable()
    wall_s = time.perf_counter() - t0
    per_iter_us = (wall_s / iterations) * 1_000_000
    rate = iterations / wall_s if wall_s > 0 else float("inf")
    print(
        f"  wall={wall_s*1000:.1f} ms  per-iter={per_iter_us:.1f} µs  "
        f"rate={rate:,.0f}/s"
    )

    # Top-N functions by cumulative time within this benchmark scope.
    buf = io.StringIO()
    stats = pstats.Stats(profiler, stream=buf)
    stats.sort_stats("cumulative")
    stats.print_stats(top)
    # Trim the header pstats prints (lines starting with whitespace+/+rank).
    print("  top functions (cumulative):")
    body_started = False
    for line in buf.getvalue().splitlines():
        if line.lstrip().startswith("ncalls"):
            body_started = True
            print(f"    {line}")
            continue
        if body_started and line.strip():
            print(f"    {line}")

    # Persist full stats to disk for follow-up inspection.
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{name}.prof"
    stats.dump_stats(str(out))

    return {
        "wall_ms": wall_s * 1000.0,
        "per_iter_us": per_iter_us,
        "rate_per_sec": rate,
    }


# ---------------------------------------------------------------------------
# 1. HTML parsing
# ---------------------------------------------------------------------------


def bench_parse_detail_python_only(iterations: int) -> Dict[str, float]:
    """Pure-Python BeautifulSoup fallback (NOT the production path).

    Imports directly from the submodule to bypass the Rust dispatcher in
    ``apps.api.parsers.__init__``. Use this only to measure the cost of
    the FROZEN Python fallback, e.g. when validating that the Rust path
    actually beats it.
    """
    from apps.api.parsers.detail_parser import parse_detail_page
    html = _load_fixture("detail_page_AVSW-067.html")
    return _bench(
        "parse_detail_python_only", iterations,
        lambda: parse_detail_page(html),
    )


def bench_parse_detail_canonical(iterations: int) -> Dict[str, float]:
    """Canonical entry: apps.api.parsers.parse_detail_page (Rust if available)."""
    from apps.api.parsers import parse_detail_page, RUST_PARSERS_AVAILABLE
    html = _load_fixture("detail_page_AVSW-067.html")
    print(f"  (RUST_PARSERS_AVAILABLE={RUST_PARSERS_AVAILABLE})")
    return _bench(
        "parse_detail_canonical", iterations,
        lambda: parse_detail_page(html),
    )


def bench_parse_detail_wrapper(iterations: int) -> Dict[str, float]:
    """Spider-side wrapper: parse_detail (includes tuple reshape)."""
    from javdb.spider.parser import parse_detail
    html = _load_fixture("detail_page_AVSW-067.html")
    return _bench(
        "parse_detail_wrapper", iterations,
        lambda: parse_detail(html, index=1, skip_sleep=True),
    )


def bench_parse_index_python_only(iterations: int) -> Dict[str, float]:
    """Pure-Python BeautifulSoup fallback (FROZEN, NOT the production path)."""
    from apps.api.parsers.index_parser import parse_index_page
    html = _load_fixture("JavDB-normal_index-page1.html")
    return _bench(
        "parse_index_python_only", iterations,
        lambda: parse_index_page(html, 1),
    )


def bench_parse_index_canonical(iterations: int) -> Dict[str, float]:
    """Canonical entry: apps.api.parsers.parse_index_page (Rust if available).

    Important: ``apps.api.parsers/__init__.py`` dispatches to ``javdb_rust_core``
    when the extension is installed. Importing from the submodule
    (``apps.api.parsers.index_parser``) bypasses that dispatcher and hits
    the FROZEN Python fallback — which is what the W4.1 baseline did
    until this correction.
    """
    from apps.api.parsers import parse_index_page, RUST_PARSERS_AVAILABLE
    html = _load_fixture("JavDB-normal_index-page1.html")
    print(f"  (RUST_PARSERS_AVAILABLE={RUST_PARSERS_AVAILABLE})")
    return _bench(
        "parse_index_canonical", iterations,
        lambda: parse_index_page(html, 1),
    )


# ---------------------------------------------------------------------------
# 2. Throttle / sleep computation
# ---------------------------------------------------------------------------


def bench_plan_sleep_no_coord(iterations: int) -> Dict[str, float]:
    """MovieSleepManager.plan_sleep with no coordinator (pure-CPU path)."""
    from javdb.spider.runtime.sleep import (
        MovieSleepManager, PenaltyTracker,
    )
    mgr = MovieSleepManager(
        sleep_min=3.0,
        sleep_max=8.0,
        penalty_tracker=PenaltyTracker(),
        throttle=None,
        coordinator=None,
    )
    return _bench(
        "plan_sleep_no_coord", iterations,
        lambda: mgr.plan_sleep(),
    )


def bench_throttle_compute(iterations: int) -> Dict[str, float]:
    """TripleWindowThrottle.wait_if_needed with capacity to spare (no sleep)."""
    from javdb.spider.runtime.sleep import TripleWindowThrottle
    # Limits set to 10^9 so even a high-iteration benchmark never hits the
    # wait branch — we want to time the index-lookup hot path, not the
    # 1-3 s random.uniform back-off. Production limits are 3/30/200, but
    # those would saturate after a few hundred calls and the rest of the
    # benchmark would be dominated by time.sleep().
    throttle = TripleWindowThrottle(
        short_max=10**9, long_max=10**9, extra_max=10**9,
    )
    return _bench(
        "throttle_compute", iterations,
        lambda: throttle.wait_if_needed(),
    )


# ---------------------------------------------------------------------------
# 3. DB layer (in-memory SQLite)
# ---------------------------------------------------------------------------


def _setup_in_memory_db() -> str:
    """Initialise a fresh in-memory SQLite history DB and return its path."""
    import tempfile
    # NB: SQLite ``:memory:`` is process-local; using a tempfile gives us a
    # path the get_db() routing layer can consistently reopen.
    fd, path = tempfile.mkstemp(suffix=".db", prefix="profile_hot_paths_")
    os.close(fd)
    from javdb.storage.db import init_db
    init_db(db_path=path, force=True)
    return path


def _seed_history(db_path: str, n_movies: int) -> None:
    """Insert ``n_movies`` synthetic rows into MovieHistory + TorrentHistory."""
    from javdb.storage.db import get_db
    with get_db(db_path) as conn:
        for i in range(n_movies):
            cur = conn.execute(
                """INSERT INTO MovieHistory
                   (Href, VideoCode, PerfectMatchIndicator, HiResIndicator,
                    DateTimeVisited)
                   VALUES (?, ?, 0, 0, '2026-05-15 12:00:00')""",
                (f"/v/PROF-{i:06d}", f"PROF-{i:06d}"),
            )
            movie_id = cur.lastrowid
            # One subtitle torrent per movie so the join returns data.
            conn.execute(
                """INSERT INTO TorrentHistory
                   (MovieHistoryId, SubtitleIndicator, CensorIndicator,
                    MagnetUri, Size, FileCount, ResolutionType)
                   VALUES (?, 1, 1, 'magnet:?xt=fake', '1GB', 1, 1080)""",
                (movie_id,),
            )


def bench_db_load_history(iterations: int) -> Dict[str, float]:
    """db_load_history with 1000 seeded rows in an in-memory DB."""
    from javdb.storage.db import db_load_history
    db_path = _setup_in_memory_db()
    try:
        _seed_history(db_path, n_movies=1000)
        return _bench(
            "db_load_history", iterations,
            lambda: db_load_history(db_path=db_path),
        )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


def bench_compute_indicators(iterations: int) -> Dict[str, float]:
    """_compute_indicators on synthetic torrent tuples (pure CPU)."""
    from javdb.storage.db import _compute_indicators
    torrents = [
        (1, 1, 1080),   # subtitle
        (1, 0, 720),    # hacked_subtitle
        (0, 1, 2160),   # no_subtitle (HiRes)
        (0, 0, 480),    # hacked_no_subtitle
    ]
    return _bench(
        "compute_indicators", iterations,
        lambda: _compute_indicators(torrents),
    )


def bench_category_to_indicators(iterations: int) -> Dict[str, float]:
    """category_to_indicators dispatch (pure dict lookup)."""
    from javdb.spider.contracts import category_to_indicators
    cats = ["subtitle", "no_subtitle", "hacked_subtitle", "hacked_no_subtitle"]
    return _bench(
        "category_to_indicators", iterations,
        lambda: [category_to_indicators(c) for c in cats],
    )


# ---------------------------------------------------------------------------
# 5. Proxy pool selection
# ---------------------------------------------------------------------------


def _build_pool(n_proxies: int = 5):
    """Build a ProxyPool with ``n_proxies`` fake proxies."""
    from javdb.proxy.pool import ProxyPool
    pool = ProxyPool()
    for i in range(n_proxies):
        pool.add_proxy(
            http_url=f"http://10.0.0.{i+1}:8080",
            https_url=f"http://10.0.0.{i+1}:8080",
            name=f"P{i+1}",
        )
    return pool


def bench_get_next_proxy_rr(iterations: int) -> Dict[str, float]:
    """Round-robin proxy selection (no health provider)."""
    pool = _build_pool()
    return _bench(
        "get_next_proxy_rr", iterations,
        lambda: pool.get_next_proxy(),
    )


def bench_get_next_proxy_weighted(iterations: int) -> Dict[str, float]:
    """Health-weighted proxy selection."""
    pool = _build_pool()
    # Stable scores so random.choices runs the full weighting math.
    pool.set_health_provider(lambda name: 0.5 + 0.1 * (hash(name) % 5))
    return _bench(
        "get_next_proxy_weighted", iterations,
        lambda: pool.get_next_proxy(),
    )


def bench_is_proxy_usable(iterations: int) -> Dict[str, float]:
    """is_proxy_usable predicate on a ProxyInfo."""
    from javdb.proxy.pool import ProxyInfo
    from javdb.proxy.policy import is_proxy_usable
    proxy = ProxyInfo(
        http_url="http://10.0.0.1:8080",
        https_url="http://10.0.0.1:8080",
        name="P1",
    )
    return _bench(
        "is_proxy_usable", iterations,
        lambda: is_proxy_usable(proxy),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


BENCHMARKS: List[Tuple[str, Callable[[int], Dict[str, float]], int]] = [
    # HTML parsing — canonical entries dispatch to Rust when the extension
    # is installed. The _python_only variants are kept for comparison with
    # the FROZEN BeautifulSoup fallback.
    ("parse_detail_canonical",    bench_parse_detail_canonical,    1_000),
    ("parse_detail_python_only",  bench_parse_detail_python_only,  100),
    ("parse_detail_wrapper",      bench_parse_detail_wrapper,      1_000),
    ("parse_index_canonical",     bench_parse_index_canonical,     500),
    ("parse_index_python_only",   bench_parse_index_python_only,   30),
    # Throttle / sleep — sub-microsecond when uncontended; large counts.
    ("plan_sleep_no_coord",       bench_plan_sleep_no_coord,       100_000),
    ("throttle_compute",          bench_throttle_compute,          100_000),
    # DB layer.
    ("db_load_history",           bench_db_load_history,           20),
    ("compute_indicators",        bench_compute_indicators,        500_000),
    ("category_to_indicators",    bench_category_to_indicators,    500_000),
    # Proxy pool.
    ("get_next_proxy_rr",         bench_get_next_proxy_rr,         200_000),
    ("get_next_proxy_weighted",   bench_get_next_proxy_weighted,   200_000),
    ("is_proxy_usable",           bench_is_proxy_usable,           1_000_000),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only", help="Run only the named benchmark (e.g. parse_detail_inner)",
    )
    parser.add_argument(
        "--scale", type=float, default=1.0,
        help="Multiplier on per-benchmark iteration counts (default 1.0)",
    )
    args = parser.parse_args()

    print(f"Profiling spider hot paths.")
    print(f"Saving full pstats dumps to: {REPORTS_DIR}/")

    results: Dict[str, Dict[str, float]] = {}
    for name, fn, default_iters in BENCHMARKS:
        if args.only and args.only != name:
            continue
        iters = max(1, int(default_iters * args.scale))
        try:
            results[name] = fn(iters)
        except Exception as e:  # noqa: BLE001 - we want partial reports
            print(f"\n  !! {name} FAILED: {e!r}")

    # Summary table sorted by per-iteration cost so the ops can scan
    # for the most-expensive call.
    print("\n══ Summary (sorted by per-iteration cost) ══")
    print(f"  {'benchmark':30s}  {'per-iter':>14s}  {'rate/s':>14s}")
    for name, r in sorted(
        results.items(), key=lambda kv: -kv[1]["per_iter_us"],
    ):
        per = r["per_iter_us"]
        unit = "µs"
        if per >= 1_000_000:
            per, unit = per / 1_000_000, "s"
        elif per >= 1_000:
            per, unit = per / 1_000, "ms"
        print(
            f"  {name:30s}  {per:>12.2f} {unit}  "
            f"{r['rate_per_sec']:>14,.0f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
