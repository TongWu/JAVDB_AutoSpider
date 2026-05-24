# IMP-ADR021-01: Implement Log Scan Cap and Explore Cache Limit

| Field       | Value                                      |
| ----------- | ------------------------------------------ |
| **Status**  | Completed                                  |
| **Date**    | 2026-05-24                                 |
| **Related** | [ADR-021](ADR-021-api-resource-bounds.md)  |

## Scope

Two independent hardening changes as specified in ADR-021:

1. Cap `logs.py` meta file scanning at 200 files, add `scanned_files` to response.
2. Cap `_DOWNLOADED_MAP_CACHE` at 8 entries with LRU eviction.

## Steps

### Phase 1: Log Scan Cap

**Files:**
- Modify: `apps/api/routers/logs.py`
- Modify: `apps/api/schemas/logs.py`

- [x] **Step 1: Add `scanned_files` to `LogSearchResponse`**

In `apps/api/schemas/logs.py`, add `scanned_files: int` field to `LogSearchResponse`.

- [x] **Step 2: Add `_MAX_META_SCAN` and apply `islice`**

In `apps/api/routers/logs.py`:
- Import `itertools.islice`
- Add `_MAX_META_SCAN = 200` constant
- Wrap the `sorted(...)` iterable with `islice(..., _MAX_META_SCAN)`
- Track `scanned_files` count
- Return `scanned_files` in the response

- [x] **Step 3: Add unit tests**

Test that:
- When meta files exceed `_MAX_META_SCAN`, only the newest N are scanned
- `scanned_files` reflects the actual number scanned
- Existing behavior is preserved when under the cap

### Phase 2: Downloaded-Map Cache Limit

**Files:**
- Modify: `apps/api/services/explore_service.py`

- [x] **Step 4: Add `_MAX_DOWNLOADED_MAP_CACHE_SIZE` and LRU eviction**

In `explore_service.py`:
- Add `_MAX_DOWNLOADED_MAP_CACHE_SIZE = 8` constant
- After the `_DOWNLOADED_MAP_CACHE[cache_key] = (now, downloaded)` write in `_downloaded_map_by_href`, add eviction: if `len(_DOWNLOADED_MAP_CACHE) > _MAX_DOWNLOADED_MAP_CACHE_SIZE`, find the key with the smallest timestamp and delete it.

- [x] **Step 5: Add unit tests**

Test that:
- Cache evicts the oldest entry when size exceeds the cap
- Eviction preserves the most recently written entries
- Normal operation (1 key) is unaffected

### Phase 3: Validate

- [x] **Step 6: Run full test suite**

```bash
pytest tests/unit/ -x -q --tb=short
```
