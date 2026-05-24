# ADR-021: API Resource Bounds — Log Scan Cap and Explore Cache Limit

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted                                                              |
| **Date**    | 2026-05-24                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-018](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md) |

## Context

A code review identified two API endpoints with unbounded resource consumption:

1. **`GET /api/logs/search`** ([logs.py](../../../apps/api/routers/logs.py)) — scans every `*.meta.json` file in `logs/jobs/` with no upper limit. Each meta file triggers a filesystem read and JSON parse. While current production volume is low (30–270 files), there is no cleanup mechanism, so the count only grows. Both I/O latency (glob + read N files synchronously) and memory (candidates list + subsequent log line scanning) are concerns.

2. **`_DOWNLOADED_MAP_CACHE`** ([explore_service.py](../../../apps/api/services/explore_service.py)) — a plain `dict` with TTL-based expiry (10 s) but no size cap. The cache key is the resolved CSV file path; in practice only one key exists (`reports/parsed_movies_history.csv`). The risk is not current but structural: if configuration changes introduce additional CSV paths, the cache grows without bound. The sibling `EXPLORE_DETAIL_CACHE` already has a `CACHE_MAX_ITEMS` guard; `_DOWNLOADED_MAP_CACHE` should match.

Both issues are low-severity today but represent missing safety nets that could cause memory exhaustion or request timeouts as data accumulates.

## Decision

### 1. Log Meta Scan Cap

Add a module-level constant `_MAX_META_SCAN = 200` in `logs.py`. The existing `sorted(..., reverse=True)` loop already processes newest-first; wrap it with `itertools.islice` to stop after `_MAX_META_SCAN` files.

**Why 200:** Production volume is 30–270 files. A cap of 200 provides ~2–3× headroom above the typical ceiling, while preventing runaway scanning if cleanup is neglected for months.

**Client observability:** Add a `scanned_files: int` field to `LogSearchResponse` so the frontend can display a notice when results were truncated by the scan cap (i.e., when `scanned_files == _MAX_META_SCAN`).

**Not configurable via env var.** The cap is a safety net, not a tuning knob. If the default proves wrong, change the constant and deploy — the same workflow as adjusting `_HARD_CAP`.

### 2. Downloaded-Map Cache Size Limit

Add a `_MAX_DOWNLOADED_MAP_CACHE_SIZE = 8` constant in `explore_service.py`. After inserting a new entry into `_DOWNLOADED_MAP_CACHE`, if `len(cache) > _MAX_DOWNLOADED_MAP_CACHE_SIZE`, evict the entry with the oldest timestamp (LRU-style, consistent with the eviction pattern in `EXPLORE_DETAIL_CACHE`).

**Why 8:** Only 1 key exists in practice. A cap of 8 is generous enough to never fire under normal use, while preventing unbounded growth if the config surface changes.

**Why LRU over hard-reject:** Hard-reject (refusing new writes) would degrade the explore feature when the cap is hit. LRU keeps the cache functional by discarding stale entries that are unlikely to be reused within the 10 s TTL window.

## Consequences

- **Log search:** Searches over very old logs (> 200 jobs ago, without date filters) may miss results. This is acceptable because (a) the endpoint is admin-only, (b) date filters narrow the scan before the cap applies, and (c) the `scanned_files` field makes truncation visible.
- **Explore cache:** No behavioral change under normal use. The LRU eviction is a no-op when only 1 CSV path is configured.
- **No new env vars.** Both caps are compile-time constants, keeping the configuration surface unchanged.
- **Testing:** Unit tests should verify (a) the scan cap truncates correctly and reports `scanned_files`, and (b) the cache evicts the oldest entry when the cap is exceeded.
