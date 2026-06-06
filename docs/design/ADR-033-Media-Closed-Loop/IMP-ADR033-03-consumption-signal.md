# IMP-ADR033-03: Consumption Signal (Media Closed-Loop Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-033](ADR-033-media-closed-loop.md) (umbrella) — this is **Phase 3** of three. It builds on [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md) (Phase 1, implemented) and assumes [IMP-ADR033-02](IMP-ADR033-02-ownership-truth.md) (Phase 2) has introduced the `--pass {acquisition,ownership,all}` CLI selector and the shared `run_ownership` sibling entrypoint. This plan **extends** that selector — it adds the `consumption` choice (and grows the meaning of `all` to include it) plus the `run_consumption` wiring; it does **not** re-introduce the selector.

**Status:** Implemented and locally verified (2026-06-06). All 12 tasks landed via subagent-driven development with two-stage (spec + quality) review per task: `ConsumptionSignal` + `UnresolvedMediaItem` D1 migrations + local mirror, consumption models, the service-side high/medium/low join-key resolver (delegating validity to `javdb.parsing.common`), the two repos (full-replace UPSERT), persistence wiring, `MEDIA_SERVERS` config parsing + masking, the `MediaServerAdapter` Protocol + Emby/Plex adapters (read-only, injected-fake tested), the `run_consumption` sole-writer pass (per-instance fail-open; resolve; persist + count unresolved), the `--pass consumption` CLI arm, workflow + `config.py.example` wiring, and bilingual docs incl. a new media-servers setup page. Verification gate passed (Phase-3 unit + smoke suite green; adapter/collector/resolver read-only grep clean; `run()`/`run_ownership` byte-unchanged). The Emby/Plex live endpoint paths + field names carry `TODO-VERIFY` markers (pinned by injected-fake unit tests; confirm against the operator's server build before first live run). Remote D1 apply and the local SQLite mirror refresh remain deployment-environment gates.

**Goal:** Capture the strongest implicit preference signal — actual watch behaviour — from the operator's media servers (Emby + Plex). A new asynchronous `run_consumption(options, *, repo=None, ...)` sibling of the Phase-1 `run()` reads every configured `MEDIA_SERVERS` instance through a pluggable `MediaServerAdapter`, resolves each raw media item to a `video_code` via a best-effort confidence ladder, and UPSERTs one `ConsumptionSignal` row per `(video_code, instance, library_id)`. Items that cannot be resolved land in a counted, persisted `UnresolvedMediaItem` bucket (no silent drops, ADR-033 D9). Per-instance failures are fail-open: one dead server never aborts the pass.

**Architecture:** Phase 3 stays inside the single `javdb/ops/reconcile/` module (ADR-033 D4). It appends `run_consumption` to `service.py` alongside `run()` (Phase 1) and `run_ownership` (Phase 2); appends `MediaItem`, `ConsumptionSignalRecord`, `UnresolvedMediaItemRecord` + confidence constants to `models.py`; adds `ConsumptionSignalRepo` + `UnresolvedMediaItemRepo` to `javdb/storage/repos/`; adds `open_consumption_repo()` / `open_unresolved_repo()` to `persistence.py`. The media-server adapters live in **new** integration packages `javdb/integrations/media_servers/{emby,plex}/` (ADR-015 seam: typed inputs, read-only `list_items(since) -> list[MediaItem]`, no DB writes). A `MediaServerAdapter` Protocol pins the contract; a thin `MediaServerCollector` wrapper in `collectors.py` keeps the read-only seam. The **join-key resolver runs in the service**, not the adapter (ADR-033 D9 / D-P3-5), reusing the existing `_is_plausible_video_code` validator + video-code family regexes in `javdb/parsing/common.py`. `ConsumptionSignal` is a full-replace UPSERT per `(video_code, instance, library_id)`; merging across instances/libraries is a derived query only (ADR-033 D8 / D-P3-7).

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `requests` (Emby REST / Plex `X-Plex-Token`), `pytest`, Cloudflare D1 + `wrangler`, GitHub Actions.

**Storage placement:** `ConsumptionSignal` and `UnresolvedMediaItem` both live in the **operations** logical DB (`javdb-operations`), alongside `AcquisitionOutcome` (Phase 1) and `OwnershipLedger` (Phase 2) — operational closed-loop data, not history/dedup, not reports/sessions.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_06_06_add_consumption_signal.sql` | Create | `ConsumptionSignal` + `UnresolvedMediaItem` DDL + indexes (D1-first) |
| `javdb/storage/db/_db_migrations.py` | Modify | Mirror both tables into `_OPERATIONS_DDL` (local SQLite bootstrap, ~after line 616) |
| `javdb/ops/reconcile/models.py` | Modify | Append `MediaItem`, `ConsumptionSignalRecord`, `UnresolvedMediaItemRecord`, `RESOLVED_CONFIDENCES`, `ConsumptionOptions`, `ConsumptionResult` |
| `javdb/ops/reconcile/code_resolver.py` | Create | `resolve_video_code(item) -> tuple[str \| None, str]` confidence ladder (reuses `javdb.parsing.common`) |
| `javdb/storage/repos/consumption_signal_repo.py` | Create | `ConsumptionSignalRepo` (idempotent upsert / get / list_by_video_code) |
| `javdb/storage/repos/unresolved_media_item_repo.py` | Create | `UnresolvedMediaItemRepo` (idempotent upsert / get) |
| `javdb/ops/reconcile/persistence.py` | Modify | Append `open_consumption_repo()` + `open_unresolved_repo()` |
| `javdb/integrations/media_servers/__init__.py` | Create | Namespace marker + `MediaServerAdapter` Protocol + `build_adapter()` factory |
| `javdb/integrations/media_servers/emby/{__init__.py,adapter.py}` | Create | `EmbyAdapter` (REST + `X-Emby-Token`) → raw `MediaItem`s |
| `javdb/integrations/media_servers/plex/{__init__.py,adapter.py}` | Create | `PlexAdapter` (`X-Plex-Token`) → raw `MediaItem`s |
| `javdb/ops/reconcile/media_config.py` | Create | `parse_media_servers(raw) -> list[MediaServerConfig]` validation + masking |
| `javdb/ops/reconcile/collectors.py` | Modify | Add read-only `MediaServerCollector` wrapper |
| `javdb/ops/reconcile/service.py` | Modify | Add `run_consumption()` (sole writer; per-instance fail-open) |
| `javdb/ops/reconcile/__init__.py` | Modify | Re-export `run_consumption`, `ConsumptionOptions`, `ConsumptionResult`, `MediaItem` |
| `apps/cli/ops/reconcile.py` | Modify | Wire the `--pass consumption` arm (Phase 2 introduced `--pass`) |
| `.github/workflows/ReconcileLibrary.yml` | Modify | Add `MEDIA_SERVERS` config-generation note (already runs `--pass all` from Phase 2) |
| `config.py.example` | Modify | Document the `MEDIA_SERVERS` list-of-dicts block |
| `CONTEXT.md` | Modify | Promote *Consumption signal* + add *Join-key resolution* / *Unresolved bucket* terms |
| `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Document the `--pass consumption` arm of the reconcile CLI |
| `docs/handbook/en/self-hoster/media-servers.md` (+ zh) | Create | New self-hoster page: configuring `MEDIA_SERVERS` (Emby/Plex), tokens, libraries |
| `docs/handbook/en/self-hoster/github-actions-setup.md` (+ zh) | Modify | Document `MEDIA_SERVERS` secret/var for `ReconcileLibrary.yml` |
| `tests/unit/test_consumption_models.py` | Create | Model/round-trip + confidence-constant tests |
| `tests/unit/test_code_resolver.py` | Create | Confidence-ladder regex tests (high/medium/low/none) |
| `tests/unit/test_consumption_signal_repo.py` | Create | Repo upsert/get tests (in-memory sqlite) |
| `tests/unit/test_unresolved_media_item_repo.py` | Create | Repo upsert/get tests (in-memory sqlite) |
| `tests/unit/test_media_config.py` | Create | `MEDIA_SERVERS` parsing/validation/masking tests |
| `tests/unit/test_emby_adapter.py` | Create | Emby JSON → `MediaItem` transform (fake HTTP) |
| `tests/unit/test_plex_adapter.py` | Create | Plex XML/JSON → `MediaItem` transform (fake HTTP) |
| `tests/unit/test_reconcile_consumption_service.py` | Create | `ConsumptionOptions → ConsumptionResult` service (fakes; fail-open) |
| `tests/smoke/test_reconcile_consumption_cli.py` | Create | CLI smoke (`--pass consumption --dry-run` exit code sane) |

**Naming contract (used across tasks — keep verbatim):**
- Models (in `javdb/ops/reconcile/models.py`):
  `MediaItem` (frozen dataclass: `instance`, `source_type`, `library_id`, `library_name`, `item_id`, `file_path`, `folder_name`, `title`, `watched`, `progress_pct`, `play_count`, `rating`, `watched_at`);
  `ConsumptionSignalRecord` (dataclass mirroring the table columns);
  `UnresolvedMediaItemRecord` (dataclass mirroring the table columns);
  `RESOLVED_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")`;
  `ConsumptionOptions` (dataclass: `servers: Sequence[MediaServerConfig]`, `dry_run: bool = False`, `since: str | None = None`);
  `ConsumptionResult` (dataclass counters: `instances_observed`, `items_observed`, `signals_updated`, `resolved_high`, `resolved_medium`, `resolved_low`, `marked_unresolved`, `errors: list[str]`).
- Config (in `javdb/ops/reconcile/media_config.py`):
  frozen `MediaServerConfig` (`source_type`, `instance`, `base_url`, `token`, `libraries: tuple[str, ...]`);
  `parse_media_servers(raw: object) -> list[MediaServerConfig]`.
- Resolver (in `javdb/ops/reconcile/code_resolver.py`):
  `resolve_video_code(item: MediaItem) -> tuple[Optional[str], str]` returning `(video_code_or_None, confidence)` where confidence ∈ `{"high","medium","low","none"}`.
- Repos:
  `ConsumptionSignalRepo(conn)` with `upsert(record)`, `get(video_code, instance, library_id)`, `list_by_video_code(video_code)`;
  `UnresolvedMediaItemRepo(conn)` with `upsert(record)`, `get(instance, library_id, item_id)`.
- Persistence: `open_consumption_repo()`, `open_unresolved_repo()`.
- Adapters (in `javdb/integrations/media_servers/`):
  `MediaServerAdapter` Protocol with `list_items(self, since: Optional[str]) -> list[MediaItem]`;
  `EmbyAdapter(config)`; `PlexAdapter(config)`; factory `build_adapter(config) -> MediaServerAdapter`.
- Collector: `MediaServerCollector(adapter).collect(since) -> list[MediaItem]` (read-only seam).
- Service fn: `run_consumption(options, *, repo=None, unresolved_repo=None, adapters=None) -> ConsumptionResult`.

> **Why `ConsumptionResult`/`ConsumptionOptions` are new (not reused).** The Phase-1
> `ReconcileOptions`/`ReconcileResult` describe an acquisition-outcome pass keyed
> by qb_hash with stall/fail thresholds — a different shape entirely. Phase 3
> gets its own typed pair, exactly as Phase 2 gets `OwnershipOptions`/`OwnershipResult`.
> All three passes are independently testable and rollback-able (ADR-033 D4).

> **Cross-phase boundary.** This IMP is **backend data layer only** (ADR-033 D-X-1).
> No web read-API, no TypeScript, no Vue. The web surface for the consumption signal
> is owned by ADR-034 Phase 3.

---

## Task 1: D1 migration — `ConsumptionSignal` + `UnresolvedMediaItem` tables

**Files:**
- Create: `javdb/migrations/d1/2026_06_06_add_consumption_signal.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (mirror into `_OPERATIONS_DDL`)

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-06-06: Add ConsumptionSignal + UnresolvedMediaItem tables (ADR-033 Phase 3).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_06_06_add_consumption_signal.sql
--
-- ConsumptionSignal records per (video_code, instance, library) watch/rating
-- evidence pulled from media servers. It is enrichment: written off the
-- Pending->Commit path, idempotent UPSERT, never merged across sources on write
-- (merging is a derived view, ADR-033 D8). UnresolvedMediaItem holds media items
-- whose video_code could not be resolved (ADR-033 D9) so the count is never lost.

CREATE TABLE IF NOT EXISTS ConsumptionSignal (
  video_code          TEXT NOT NULL,
  source_type         TEXT NOT NULL,   -- emby | plex
  instance            TEXT NOT NULL,   -- configured connection id, e.g. plex-home
  library_id          TEXT NOT NULL,
  library_name        TEXT,
  watched             INTEGER,
  progress_pct        INTEGER,
  play_count          INTEGER,
  rating              REAL,
  watched_at          TEXT,
  resolved_confidence TEXT,            -- high | medium | low
  observed_at         TEXT,
  PRIMARY KEY (video_code, instance, library_id)
);

CREATE INDEX IF NOT EXISTS idx_consumption_video_code ON ConsumptionSignal(video_code);
CREATE INDEX IF NOT EXISTS idx_consumption_instance_library ON ConsumptionSignal(instance, library_id);

-- PK is (instance, library_id, item_id) and deliberately EXCLUDES source_type:
-- re-observing the same server-side item UPSERTs in place (no duplicate rows),
-- while source_type is recorded for audit only and never defines uniqueness
-- (adapters are independent; one instance is always exactly one source_type).
CREATE TABLE IF NOT EXISTS UnresolvedMediaItem (
  instance     TEXT NOT NULL,
  source_type  TEXT,
  library_id   TEXT NOT NULL,
  library_name TEXT,
  item_id      TEXT NOT NULL,
  raw_title    TEXT,
  file_path    TEXT,
  observed_at  TEXT,
  PRIMARY KEY (instance, library_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_unresolved_instance ON UnresolvedMediaItem(instance);
```

- [ ] **Step 2: Apply to D1 (operations)** — *deployment-environment gate*

Run:
```bash
wrangler d1 execute javdb-operations --remote \
  --file=javdb/migrations/d1/2026_06_06_add_consumption_signal.sql
```
Expected: `wrangler` reports the statements executed without error. **This step needs Cloudflare credentials; run it in the same environment other migrations are applied (see Status note / Plan Self-Review).**

- [ ] **Step 3: Re-align the local SQLite mirror from D1 (D1-canonical rule)** — *deployment-environment gate*

Run:
```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: log shows `ConsumptionSignal` and `UnresolvedMediaItem` rebuilt from D1's verbatim DDL; exit code 0.

- [ ] **Step 4: Mirror both tables into the local SQLite bootstrap DDL**

The local bootstrap DDL lives in `javdb/storage/db/_db_migrations.py` in the `_OPERATIONS_DDL` constant (the operations block ends at the closing `"""` on line 617, right after the `AcquisitionOutcome` indexes on lines 613-616). Append the two new tables **inside** that string, before the closing `"""`:

```python
CREATE TABLE IF NOT EXISTS ConsumptionSignal (
    video_code          TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    instance            TEXT NOT NULL,
    library_id          TEXT NOT NULL,
    library_name        TEXT,
    watched             INTEGER,
    progress_pct        INTEGER,
    play_count          INTEGER,
    rating              REAL,
    watched_at          TEXT,
    resolved_confidence TEXT,
    observed_at         TEXT,
    PRIMARY KEY (video_code, instance, library_id)
);
CREATE INDEX IF NOT EXISTS idx_consumption_video_code ON ConsumptionSignal(video_code);
CREATE INDEX IF NOT EXISTS idx_consumption_instance_library ON ConsumptionSignal(instance, library_id);

CREATE TABLE IF NOT EXISTS UnresolvedMediaItem (
    instance     TEXT NOT NULL,
    source_type  TEXT,
    library_id   TEXT NOT NULL,
    library_name TEXT,
    item_id      TEXT NOT NULL,
    raw_title    TEXT,
    file_path    TEXT,
    observed_at  TEXT,
    PRIMARY KEY (instance, library_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_unresolved_instance ON UnresolvedMediaItem(instance);
```

> Keep the DDL byte-compatible with the `.sql` migration (same column order, same
> PK). The `.sql` file is the D1 authority; this mirror only exists so fresh local
> bootstrap and in-memory tests have the tables (mirror Phase 1's approach).

- [ ] **Step 5: Verify the tables exist locally**

Run:
```bash
python3 -c "import javdb.storage.db._db_migrations as m; assert 'ConsumptionSignal' in m._OPERATIONS_DDL and 'UnresolvedMediaItem' in m._OPERATIONS_DDL; print('ddl ok')"
```
Expected: `ddl ok`

- [ ] **Step 6: Commit**

```bash
git add javdb/migrations/d1/2026_06_06_add_consumption_signal.sql javdb/storage/db/_db_migrations.py
git commit -m "feat(db): add ConsumptionSignal + UnresolvedMediaItem tables (ADR-033 Phase 3)"
```

---

## Task 2: Typed models — `MediaItem`, `ConsumptionSignalRecord`, `UnresolvedMediaItemRecord`

**Files:**
- Modify: `javdb/ops/reconcile/models.py` (append; do not rewrite Phase-1/2 models)
- Test: `tests/unit/test_consumption_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_consumption_models.py
from javdb.ops.reconcile.models import (
    ConsumptionResult,
    ConsumptionSignalRecord,
    MediaItem,
    RESOLVED_CONFIDENCES,
    UnresolvedMediaItemRecord,
)


def test_resolved_confidences_are_high_medium_low():
    assert RESOLVED_CONFIDENCES == ("high", "medium", "low")


def test_media_item_is_frozen():
    item = MediaItem(
        instance="plex-home", source_type="plex", library_id="3",
        library_name="JAV", item_id="998", file_path="/m/ABC-123.mp4",
        folder_name="ABC-123", title="ABC-123 Title",
        watched=True, progress_pct=100, play_count=2, rating=8.0,
        watched_at="2026-06-06T00:00:00Z",
    )
    assert item.instance == "plex-home"
    assert item.watched is True


def test_consumption_signal_record_defaults():
    rec = ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3",
    )
    assert rec.library_name is None
    assert rec.watched is None
    assert rec.resolved_confidence is None


def test_unresolved_record_minimal():
    rec = UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42",
    )
    assert rec.source_type is None
    assert rec.raw_title is None


def test_consumption_result_starts_empty():
    res = ConsumptionResult()
    assert res.items_observed == 0
    assert res.marked_unresolved == 0
    assert res.errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_consumption_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'MediaItem'`

- [ ] **Step 3: Append the models** to `javdb/ops/reconcile/models.py`

The file already defines `AcquisitionOutcomeRecord`, `Observation`, `ReconcileOptions`, `ReconcileResult` and `utc_now_iso` (models.py:36-77). Append the Phase-3 contracts at the end (after Phase 2's ownership models). `Optional`, `Sequence`, `dataclass`, `field` are already imported at the top of the file (models.py:5-7).

```python
# ── ADR-033 Phase 3: consumption signal ────────────────────────────────────

RESOLVED_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")


@dataclass(frozen=True)
class MediaItem:
    """Raw, read-only item returned by a MediaServerAdapter (never persisted as-is).

    The adapter fills the descriptive + signal fields; the service resolves a
    video_code from file_path/folder_name/title and decides confidence."""

    instance: str
    source_type: str            # 'emby' | 'plex'
    library_id: str
    library_name: Optional[str] = None
    item_id: str = ""
    file_path: Optional[str] = None
    folder_name: Optional[str] = None
    title: Optional[str] = None
    watched: Optional[bool] = None
    progress_pct: Optional[int] = None
    play_count: Optional[int] = None
    rating: Optional[float] = None
    watched_at: Optional[str] = None


@dataclass
class ConsumptionSignalRecord:
    video_code: str
    source_type: str
    instance: str
    library_id: str
    library_name: Optional[str] = None
    watched: Optional[bool] = None
    progress_pct: Optional[int] = None
    play_count: Optional[int] = None
    rating: Optional[float] = None
    watched_at: Optional[str] = None
    resolved_confidence: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass
class UnresolvedMediaItemRecord:
    instance: str
    library_id: str
    item_id: str
    source_type: Optional[str] = None
    library_name: Optional[str] = None
    raw_title: Optional[str] = None
    file_path: Optional[str] = None
    observed_at: Optional[str] = None


@dataclass
class ConsumptionOptions:
    # Sequence[MediaServerConfig]; typed loosely to avoid a models→media_config
    # import cycle (media_config imports nothing from models).
    servers: Sequence[object] = ()
    dry_run: bool = False
    since: Optional[str] = None


@dataclass
class ConsumptionResult:
    instances_observed: int = 0
    items_observed: int = 0
    signals_updated: int = 0
    resolved_high: int = 0
    resolved_medium: int = 0
    resolved_low: int = 0
    marked_unresolved: int = 0
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_consumption_models.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/reconcile/models.py tests/unit/test_consumption_models.py
git commit -m "feat(reconcile): add consumption-signal models (ADR-033 Phase 3)"
```

---

## Task 3: Join-key resolver (confidence ladder)

**Files:**
- Create: `javdb/ops/reconcile/code_resolver.py`
- Test: `tests/unit/test_code_resolver.py`

> **Reuse audit (REQUIRED before writing regexes).** I searched `javdb/spider/`
> and `javdb/parsing/` for a general filename→code extractor:
> - `javdb/spider/filename_helper.py` is **CSV output-naming only** (it turns a
>   custom URL into a CSV filename); it has no basename→code regex.
> - `javdb/spider/url_helper.py` (`extract_url_identifier`) extracts an identifier
>   from a JavDB **URL path** (`/video_codes/...`), not from an arbitrary filename.
> - `javdb/integrations/rclone/helper.py` `parse_folder_name` (Rust at
>   `rust_core/src/rclone_ops.rs:14-40`, Python fallback `_py_parse_folder_name`
>   at helper.py:514-538) only parses the **legacy** `CODE [sensor-subtitle]`
>   folder form — not a general extractor.
> - `javdb/spider/contracts.extract_video_code` / `javdb/parsing/common.extract_video_code`
>   take a BeautifulSoup `Tag`, not a string.
>
> **What DOES exist and is reused:** `javdb/parsing/common.py` exposes
> `_is_plausible_video_code(raw)` (common.py:189) and the video-code family regex
> tuple `_VIDEO_CODE_FAMILY_PATTERNS` + `classify_video_code_family(raw)`
> (common.py:161-186), which already encode the canonical code shapes
> (`classic_hyphenated` `^[A-Za-z]+-\d+[A-Za-z0-9]*$`, `multi_hyphen`,
> `hyphenless_studio`, numeric-date, western-studio-date).
>
> **Decision:** there is **no** reusable general string→code extractor, so this
> task writes a NEW resolver that *tokenizes* the basename/folder/title and
> *validates* each candidate with the existing `_is_plausible_video_code`
> (treating `classify_video_code_family != ''` as a stronger match for the high
> tier). The regexes that find candidate tokens are TDD-pinned here; the
> validity decision stays delegated to `javdb/parsing/common` so the two engines
> never drift.
>
> **[Shared audit with [IMP-ADR033-02](IMP-ADR033-02-ownership-truth.md) Task 8].**
> IMP-02's dedup-migration task ran the same reuse audit and likewise delegates
> code validity to `javdb.parsing.common` (`_is_plausible_video_code` /
> `classify_video_code_family`). Both this resolver and the dedup Ledger reader
> depend on those validators — if `javdb/parsing/common.py` changes its accepted
> code shapes, re-audit **both** tasks together.

- [ ] **Step 1: Write the failing test**

```python
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
    code, conf = resolve_video_code(_item(file_path="/m/ｓｓｎｉ-001.mkv"))
    assert code == "SSNI-001"
    assert conf == "high"


def test_medium_confidence_from_folder_name_when_path_has_no_code():
    code, conf = resolve_video_code(
        _item(file_path="/movies/disc1/title.mkv", folder_name="STARS-789 [4K]")
    )
    assert code == "STARS-789"
    assert conf == "medium"


def test_medium_confidence_from_title_when_no_path_or_folder_code():
    code, conf = resolve_video_code(_item(title="MIDV-001 Some Title Words"))
    assert code == "MIDV-001"
    assert conf == "medium"


def test_low_confidence_loose_fallback():
    # No clean delimited token, but a loose alnum run that still validates.
    code, conf = resolve_video_code(_item(title="watch n0656 now"))
    assert code == "N0656"
    assert conf == "low"


def test_none_when_no_plausible_code():
    code, conf = resolve_video_code(_item(title="Family Vacation 2024", file_path="/m/clip.mp4"))
    assert code is None
    assert conf == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_code_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.reconcile.code_resolver`

- [ ] **Step 3: Write the resolver**

```python
# javdb/ops/reconcile/code_resolver.py
"""Best-effort join-key resolution for media items (ADR-033 D9 / D-P3-5).

Resolves a video_code from a media item's file_path / folder_name / title via a
confidence ladder. Validity of any candidate token is delegated to
javdb.parsing.common so the resolver never re-invents (or drifts from) the
canonical video-code shapes. Codes are normalized with the same
NFKC + strip + upper idiom as the dedup checker (dedup._normalise_code).
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

from javdb.ops.reconcile.models import MediaItem
from javdb.parsing.common import (
    _is_plausible_video_code,
    classify_video_code_family,
)

# A delimited candidate token: letters/digits with -/_ separators, the shape a
# clean code lives in (ABC-123, FC2-PPV-123456, 062216-179, n0656). The loose
# fallback splits free text into the same token alphabet.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _normalise_code(raw: str) -> str:
    """NFKC + strip + upper — identical to dedup._normalise_code (dedup.py:18-30)."""
    return unicodedata.normalize("NFKC", raw or "").strip().upper()


def _first_plausible(text: Optional[str]) -> Optional[str]:
    """Return the first NFKC-normalized token in *text* that validates, else None.

    Prefers a token recognized by a known code family over a merely-plausible
    one, so 'disc1 STARS-789' resolves to STARS-789 rather than 'DISC1'."""
    if not text:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    family_hit: Optional[str] = None
    plausible_hit: Optional[str] = None
    for raw_token in _TOKEN_RE.findall(normalized):
        token = raw_token.strip().upper()
        if classify_video_code_family(token):
            family_hit = family_hit or token
        elif _is_plausible_video_code(token):
            plausible_hit = plausible_hit or token
    return family_hit or plausible_hit


def resolve_video_code(item: MediaItem) -> tuple[Optional[str], str]:
    """Resolve (video_code, confidence) for a media item.

    Ladder (ADR-033 D9):
      high   — code found in the file_path basename
      medium — code found in folder_name or title
      low    — code found via a loose scan of the title (weaker source)
      none   — nothing plausible → caller routes to UnresolvedMediaItem
    """
    # high: strict scan of the file basename.
    if item.file_path:
        basename = os.path.basename(item.file_path)
        stem = os.path.splitext(basename)[0]
        hit = _first_plausible(stem)
        if hit:
            return hit, "high"

    # medium: folder_name, then title.
    for source in (item.folder_name, item.title):
        hit = _first_plausible(source)
        if hit:
            return hit, "medium"

    # low: a looser pass over the title already happened above via _first_plausible;
    # the distinction here is provenance only — if we reach this branch the title
    # had no family/plausible token, so there is no low-confidence code either.
    return None, "none"
```

> **On the `low` tier.** The brief calls for a 4-rung ladder. In practice the
> `_first_plausible` scan over `title` already covers the looser case, so a code
> found *only* in free-text title is the natural `low` tier. To honour the brief's
> explicit `low` rung, **classify by source rather than collapsing**: emit `high`
> for a basename hit, `medium` for a folder_name hit, and `low` for a title-only
> hit. Refine `resolve_video_code` so the title branch returns `low`, not `medium`
> (the test `test_low_confidence_loose_fallback` pins a title-only `n0656` → `low`,
> and `test_medium_confidence_from_title_when_no_path_or_folder_code` must then be
> re-pinned to expect `low` for a title-only delimited code, OR keep a delimited
> title hit at `medium` and a *non-family loose* hit at `low`). **Pick one rule
> and pin it with the tests in Step 1 — do not ship an ambiguous ladder.** The
> recommended rule, encoded below, is: basename→`high`; folder_name→`medium`;
> title with a *family-recognized* token→`medium`; title with only a *plausible
> (non-family)* token→`low`; nothing→`none`.

- [ ] **Step 4: Finalize the ladder rule (resolve the title tier)**

Replace the `medium`/`low` block so a family-recognized title token is `medium`
and a merely-plausible title token is `low`:

```python
    # medium: folder_name (family or plausible), then a family-recognized title token.
    folder_hit = _first_plausible(item.folder_name)
    if folder_hit:
        return folder_hit, "medium"

    if item.title:
        normalized = unicodedata.normalize("NFKC", item.title)
        family_token = None
        plausible_token = None
        for raw_token in _TOKEN_RE.findall(normalized):
            token = raw_token.strip().upper()
            if classify_video_code_family(token):
                family_token = family_token or token
            elif _is_plausible_video_code(token):
                plausible_token = plausible_token or token
        if family_token:
            return family_token, "medium"
        if plausible_token:
            return plausible_token, "low"

    return None, "none"
```

Update the Step-1 tests so each tier is pinned unambiguously:
- `MIDV-001` (a `classic_hyphenated` family code) in a title → `medium`.
- `n0656` (`hyphenless_studio`, but as the *loose* free-text case) → ensure the
  test reflects whichever tier the final rule assigns; `n0656` matches the
  `hyphenless_studio` family, so under this rule a title-only `n0656` is
  `medium`. **If you want a true `low` example, use a plausible-but-non-family
  token** (e.g. a date-style `062216` is numeric-only → rejected by
  `_is_plausible_video_code`'s "must contain a letter or a separator" guard, so
  pick `259LUXU-1234`, which `_is_plausible_video_code` accepts but
  `classify_video_code_family` returns `''` for — per the docstring at
  common.py:176-178). Pin `test_low_confidence_loose_fallback` to
  `title="watch 259LUXU-1234 now"` → `("259LUXU-1234", "low")`.

> Verify the chosen low-tier example against the real validators before pinning:
> `python3 -c "from javdb.parsing.common import _is_plausible_video_code, classify_video_code_family as f; print(_is_plausible_video_code('259LUXU-1234'), repr(f('259LUXU-1234')))"`
> Expected: `True ''` (plausible but no family → the `low` tier). If your build
> disagrees, choose another token that prints `True ''` and pin that instead.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_code_resolver.py -v`
Expected: PASS (all rungs green; each tier pinned to a distinct source).

- [ ] **Step 6: Commit**

```bash
git add javdb/ops/reconcile/code_resolver.py tests/unit/test_code_resolver.py
git commit -m "feat(reconcile): add media-item join-key resolver (ADR-033 D9)"
```

---

## Task 4: `ConsumptionSignalRepo` + `UnresolvedMediaItemRepo`

**Files:**
- Create: `javdb/storage/repos/consumption_signal_repo.py`
- Create: `javdb/storage/repos/unresolved_media_item_repo.py`
- Test: `tests/unit/test_consumption_signal_repo.py`, `tests/unit/test_unresolved_media_item_repo.py`

Both repos copy the `AcquisitionOutcomeRepo` pattern (acquisition_outcome_repo.py:42-99): `_COLUMNS` tuple, `_row_to_record`, idempotent `INSERT ... ON CONFLICT(<pk>) DO UPDATE`. `ConsumptionSignal` is a **full-replace** UPSERT (latest observation per source wins, ADR-033 D-P3-7), so every non-PK column is set to `excluded.*` (no COALESCE).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_consumption_signal_repo.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import ConsumptionSignalRecord
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo

_DDL = """
CREATE TABLE ConsumptionSignal (
  video_code TEXT NOT NULL, source_type TEXT NOT NULL, instance TEXT NOT NULL,
  library_id TEXT NOT NULL, library_name TEXT, watched INTEGER, progress_pct INTEGER,
  play_count INTEGER, rating REAL, watched_at TEXT, resolved_confidence TEXT,
  observed_at TEXT, PRIMARY KEY (video_code, instance, library_id)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ConsumptionSignalRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, rating=8.0, resolved_confidence="high",
        observed_at="t",
    ))
    got = repo.get("ABC-123", "plex-home", "3")
    assert got.watched == 1
    assert got.rating == 8.0
    assert got.resolved_confidence == "high"


def test_upsert_full_replace_latest_wins(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=False, play_count=1, observed_at="t1",
    ))
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, play_count=3, observed_at="t2",
    ))
    got = repo.get("ABC-123", "plex-home", "3")
    assert got.watched == 1          # replaced, not coalesced
    assert got.play_count == 3
    assert got.observed_at == "t2"


def test_distinct_instances_are_separate_rows(repo):
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="plex", instance="plex-home",
        library_id="3", watched=True, observed_at="t",
    ))
    repo.upsert(ConsumptionSignalRecord(
        video_code="ABC-123", source_type="emby", instance="emby-nas",
        library_id="7", watched=False, observed_at="t",
    ))
    rows = repo.list_by_video_code("ABC-123")
    assert len(rows) == 2
    assert {r.instance for r in rows} == {"plex-home", "emby-nas"}
```

```python
# tests/unit/test_unresolved_media_item_repo.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import UnresolvedMediaItemRecord
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo

_DDL = """
CREATE TABLE UnresolvedMediaItem (
  instance TEXT NOT NULL, source_type TEXT, library_id TEXT NOT NULL,
  library_name TEXT, item_id TEXT NOT NULL, raw_title TEXT, file_path TEXT,
  observed_at TEXT, PRIMARY KEY (instance, library_id, item_id)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return UnresolvedMediaItemRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(UnresolvedMediaItemRecord(
        instance="emby-nas", source_type="emby", library_id="7", item_id="42",
        raw_title="Mystery Movie", file_path="/m/x.mkv", observed_at="t",
    ))
    got = repo.get("emby-nas", "7", "42")
    assert got.raw_title == "Mystery Movie"


def test_reobservation_is_idempotent(repo):
    rec = UnresolvedMediaItemRecord(
        instance="emby-nas", library_id="7", item_id="42", observed_at="t1",
    )
    repo.upsert(rec)
    rec.observed_at = "t2"
    repo.upsert(rec)
    got = repo.get("emby-nas", "7", "42")
    assert got.observed_at == "t2"
    assert repo._conn.execute("SELECT COUNT(*) FROM UnresolvedMediaItem").fetchone()[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_consumption_signal_repo.py tests/unit/test_unresolved_media_item_repo.py -v`
Expected: FAIL — `ModuleNotFoundError` for both repo modules.

- [ ] **Step 3: Write `ConsumptionSignalRepo`**

```python
# javdb/storage/repos/consumption_signal_repo.py
"""Repository for ADR-033 ConsumptionSignal rows (operations DB).

Full-replace UPSERT keyed by (video_code, instance, library_id): the latest
observation per source wins (ADR-033 D-P3-7). Cross-instance / cross-library
rows are naturally distinct, preserving provenance (ADR-033 D8)."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import ConsumptionSignalRecord

_COLUMNS = (
    "video_code", "source_type", "instance", "library_id", "library_name",
    "watched", "progress_pct", "play_count", "rating", "watched_at",
    "resolved_confidence", "observed_at",
)
_PK = ("video_code", "instance", "library_id")


def _row_to_record(row: Any) -> ConsumptionSignalRecord:
    return ConsumptionSignalRecord(**{c: row[c] for c in _COLUMNS})


class ConsumptionSignalRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: ConsumptionSignalRecord) -> None:
        values = [
            int(v) if isinstance(v := getattr(record, c), bool) else v
            for c in _COLUMNS
        ]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        conflict = ", ".join(_PK)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO ConsumptionSignal ({columns})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, video_code: str, instance: str, library_id: str) -> Optional[ConsumptionSignalRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ConsumptionSignal "
            f"WHERE video_code = ? AND instance = ? AND library_id = ?",
            [video_code, instance, library_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_by_video_code(self, video_code: str) -> list[ConsumptionSignalRecord]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ConsumptionSignal WHERE video_code = ?",
            [video_code],
        ).fetchall()
        return [_row_to_record(r) for r in rows]
```

- [ ] **Step 4: Write `UnresolvedMediaItemRepo`**

```python
# javdb/storage/repos/unresolved_media_item_repo.py
"""Repository for ADR-033 UnresolvedMediaItem rows (operations DB).

Media items whose video_code could not be resolved (ADR-033 D9). Keyed by
(instance, library_id, item_id) so re-observations dedupe instead of piling up."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import UnresolvedMediaItemRecord

_COLUMNS = (
    "instance", "source_type", "library_id", "library_name",
    "item_id", "raw_title", "file_path", "observed_at",
)
_PK = ("instance", "library_id", "item_id")


def _row_to_record(row: Any) -> UnresolvedMediaItemRecord:
    return UnresolvedMediaItemRecord(**{c: row[c] for c in _COLUMNS})


class UnresolvedMediaItemRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: UnresolvedMediaItemRecord) -> None:
        values = [getattr(record, c) for c in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        conflict = ", ".join(_PK)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO UnresolvedMediaItem ({columns})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, instance: str, library_id: str, item_id: str) -> Optional[UnresolvedMediaItemRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM UnresolvedMediaItem "
            f"WHERE instance = ? AND library_id = ? AND item_id = ?",
            [instance, library_id, item_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_consumption_signal_repo.py tests/unit/test_unresolved_media_item_repo.py -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/repos/consumption_signal_repo.py javdb/storage/repos/unresolved_media_item_repo.py \
       tests/unit/test_consumption_signal_repo.py tests/unit/test_unresolved_media_item_repo.py
git commit -m "feat(db): add ConsumptionSignalRepo + UnresolvedMediaItemRepo (ADR-033 Phase 3)"
```

---

## Task 5: Persistence wiring (`open_consumption_repo` / `open_unresolved_repo`)

**Files:**
- Modify: `javdb/ops/reconcile/persistence.py` (append two helpers)

> No new unit test here — exercised end-to-end by Task 8's service tests (which inject repos) and the CLI smoke (Task 9). Mirror the existing `open_outcome_repo` (persistence.py:12-21), which resolves `_db.OPERATIONS_DB_PATH` at **call time** (BFR-016) so pytest's path monkeypatch is honoured. Do **not** bind `OPERATIONS_DB_PATH` at import.

- [ ] **Step 1: Append the helpers** to `javdb/ops/reconcile/persistence.py`

```python
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo


@contextlib.contextmanager
def open_consumption_repo():
    """Yield a ConsumptionSignalRepo over the operations DB connection.

    Path resolved at call time (_db.OPERATIONS_DB_PATH) per BFR-016, matching
    open_outcome_repo."""
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield ConsumptionSignalRepo(conn)


@contextlib.contextmanager
def open_unresolved_repo():
    """Yield an UnresolvedMediaItemRepo over the operations DB connection."""
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield UnresolvedMediaItemRepo(conn)
```

- [ ] **Step 2: Verify imports**

Run: `python3 -c "from javdb.ops.reconcile.persistence import open_consumption_repo, open_unresolved_repo; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add javdb/ops/reconcile/persistence.py
git commit -m "feat(reconcile): wire consumption + unresolved persistence to operations DB"
```

---

## Task 6: `MEDIA_SERVERS` config parsing + masking

**Files:**
- Create: `javdb/ops/reconcile/media_config.py`
- Test: `tests/unit/test_media_config.py`

`MEDIA_SERVERS` is a PROXY_POOL-style list of dicts (config.py.example:114-145 is the precedent). `parse_media_servers` validates each entry, normalizes `libraries`, and produces frozen `MediaServerConfig`. Tokens are masked via `javdb.infra.masking.mask_full` (masking.py:37) in any `__repr__`/log.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_media_config.py
import pytest

from javdb.ops.reconcile.media_config import MediaServerConfig, parse_media_servers


def test_parse_minimal_emby():
    cfgs = parse_media_servers([
        {"type": "emby", "instance": "emby-nas",
         "base_url": "http://nas:8096", "token": "SECRET"},
    ])
    assert len(cfgs) == 1
    c = cfgs[0]
    assert c.source_type == "emby"
    assert c.instance == "emby-nas"
    assert c.base_url == "http://nas:8096"
    assert c.token == "SECRET"
    assert c.libraries == ()


def test_parse_plex_with_libraries():
    cfgs = parse_media_servers([
        {"type": "plex", "instance": "plex-home", "base_url": "http://h:32400",
         "token": "T", "libraries": ["JAV", "Movies"]},
    ])
    assert cfgs[0].libraries == ("JAV", "Movies")


def test_token_is_masked_in_repr():
    cfgs = parse_media_servers([
        {"type": "plex", "instance": "p", "base_url": "http://h", "token": "supersecret"},
    ])
    text = repr(cfgs[0])
    assert "supersecret" not in text


def test_rejects_unknown_type():
    with pytest.raises(ValueError, match="unsupported media server type"):
        parse_media_servers([{"type": "jellyfin", "instance": "j",
                              "base_url": "http://h", "token": "T"}])


def test_rejects_missing_required_field():
    with pytest.raises(ValueError, match="missing"):
        parse_media_servers([{"type": "emby", "instance": "e", "token": "T"}])  # no base_url


def test_rejects_duplicate_instance():
    with pytest.raises(ValueError, match="duplicate instance"):
        parse_media_servers([
            {"type": "emby", "instance": "dup", "base_url": "http://a", "token": "T"},
            {"type": "plex", "instance": "dup", "base_url": "http://b", "token": "T"},
        ])


def test_empty_or_none_returns_empty_list():
    assert parse_media_servers(None) == []
    assert parse_media_servers([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_media_config.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.reconcile.media_config`

- [ ] **Step 3: Write the config parser**

```python
# javdb/ops/reconcile/media_config.py
"""Parse + validate the MEDIA_SERVERS config list (ADR-033 D-P3-1).

MEDIA_SERVERS is a PROXY_POOL-style list of dicts in config.py (already
encrypted at rest). Tokens stay inline; they are masked in every repr/log via
javdb.infra.masking.mask_full."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from javdb.infra.masking import mask_full

_SUPPORTED_TYPES = frozenset({"emby", "plex"})
_REQUIRED = ("type", "instance", "base_url", "token")


@dataclass(frozen=True)
class MediaServerConfig:
    source_type: str
    instance: str
    base_url: str
    token: str = field(repr=False)        # never auto-repr the raw token
    libraries: Tuple[str, ...] = ()

    def __repr__(self) -> str:  # masked token for safe logging
        return (
            f"MediaServerConfig(source_type={self.source_type!r}, "
            f"instance={self.instance!r}, base_url={self.base_url!r}, "
            f"token={mask_full(self.token)!r}, libraries={self.libraries!r})"
        )


def parse_media_servers(raw: object) -> list[MediaServerConfig]:
    """Validate the MEDIA_SERVERS list into typed configs. Raises ValueError on
    a malformed entry (fail-fast on config, not at runtime)."""
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("MEDIA_SERVERS must be a list of dicts")

    configs: list[MediaServerConfig] = []
    seen_instances: set[str] = set()
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"MEDIA_SERVERS[{idx}] must be a dict")
        for key in _REQUIRED:
            if not str(entry.get(key, "")).strip():
                raise ValueError(f"MEDIA_SERVERS[{idx}] missing required field: {key}")
        source_type = str(entry["type"]).strip().lower()
        if source_type not in _SUPPORTED_TYPES:
            raise ValueError(
                f"MEDIA_SERVERS[{idx}] unsupported media server type: {entry['type']!r} "
                f"(supported: {sorted(_SUPPORTED_TYPES)})"
            )
        instance = str(entry["instance"]).strip()
        if instance in seen_instances:
            raise ValueError(f"MEDIA_SERVERS[{idx}] duplicate instance: {instance!r}")
        seen_instances.add(instance)
        libraries = tuple(
            str(lib).strip() for lib in (entry.get("libraries") or ()) if str(lib).strip()
        )
        configs.append(MediaServerConfig(
            source_type=source_type,
            instance=instance,
            base_url=str(entry["base_url"]).strip().rstrip("/"),
            token=str(entry["token"]),
            libraries=libraries,
        ))
    return configs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_media_config.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/reconcile/media_config.py tests/unit/test_media_config.py
git commit -m "feat(reconcile): parse + mask MEDIA_SERVERS config (ADR-033 D-P3-1)"
```

---

## Task 7: Media-server adapters (`MediaServerAdapter` Protocol + Emby + Plex)

**Files:**
- Create: `javdb/integrations/media_servers/__init__.py` (Protocol + `build_adapter`)
- Create: `javdb/integrations/media_servers/emby/__init__.py`, `.../emby/adapter.py`
- Create: `javdb/integrations/media_servers/plex/__init__.py`, `.../plex/adapter.py`
- Test: `tests/unit/test_emby_adapter.py`, `tests/unit/test_plex_adapter.py`

Adapters follow the ADR-015 seam (mirror `javdb/integrations/qb/uploader/`'s package shape): typed input (`MediaServerConfig`), read-only `list_items(since) -> list[MediaItem]`, **no DB writes**. They normalize the server's JSON into raw `MediaItem`s and stop — the service does join-key resolution. Adapters take an injectable HTTP caller so tests never hit the network.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_emby_adapter.py
from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.integrations.media_servers.emby.adapter import EmbyAdapter


class _FakeHttp:
    """Returns canned JSON per (url, params) the adapter requests."""
    def __init__(self, library_json, items_json):
        self._library_json = library_json
        self._items_json = items_json

    def get_json(self, path, params=None):
        if "Views" in path or "Library" in path:
            return self._library_json
        return self._items_json


def test_emby_lists_items_with_signal():
    cfg = MediaServerConfig("emby", "emby-nas", "http://nas:8096", "TOKEN", ("JAV",))
    http = _FakeHttp(
        library_json={"Items": [{"Id": "lib7", "Name": "JAV"}]},
        items_json={"Items": [{
            "Id": "42", "Name": "ABC-123 Title",
            "Path": "/media/ABC-123/ABC-123.mp4",
            "UserData": {"Played": True, "PlayCount": 2, "PlayedPercentage": 100},
        }]},
    )
    items = EmbyAdapter(cfg, http=http).list_items(since=None)
    assert len(items) == 1
    it = items[0]
    assert it.instance == "emby-nas"
    assert it.source_type == "emby"
    assert it.library_id == "lib7"
    assert it.library_name == "JAV"
    assert it.item_id == "42"
    assert it.file_path == "/media/ABC-123/ABC-123.mp4"
    assert it.watched is True
    assert it.play_count == 2
    assert it.progress_pct == 100
```

```python
# tests/unit/test_plex_adapter.py
from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.integrations.media_servers.plex.adapter import PlexAdapter


class _FakeHttp:
    def __init__(self, sections_json, items_json):
        self._sections_json = sections_json
        self._items_json = items_json

    def get_json(self, path, params=None):
        if path.endswith("/sections") or "library/sections" in path and path.count("/") <= 3:
            return self._sections_json
        return self._items_json


def test_plex_lists_items_with_signal():
    cfg = MediaServerConfig("plex", "plex-home", "http://h:32400", "T", ("JAV",))
    http = _FakeHttp(
        sections_json={"MediaContainer": {"Directory": [
            {"key": "3", "title": "JAV", "type": "movie"},
        ]}},
        items_json={"MediaContainer": {"Metadata": [{
            "ratingKey": "998", "title": "STARS-789",
            "viewCount": 1, "viewOffset": 0, "duration": 1000,
            "userRating": 8.0,
            "Media": [{"Part": [{"file": "/m/STARS-789/STARS-789.mkv"}]}],
        }]}},
    )
    items = PlexAdapter(cfg, http=http).list_items(since=None)
    assert len(items) == 1
    it = items[0]
    assert it.instance == "plex-home"
    assert it.source_type == "plex"
    assert it.library_id == "3"
    assert it.item_id == "998"
    assert it.file_path == "/m/STARS-789/STARS-789.mkv"
    assert it.watched is True       # viewCount >= 1
    assert it.rating == 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_emby_adapter.py tests/unit/test_plex_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError` for the adapter modules.

- [ ] **Step 3: Write the namespace + Protocol + factory**

```python
# javdb/integrations/media_servers/__init__.py
"""Media-server adapters for the ADR-033 consumption signal.

Each adapter is a read-only integration (ADR-015 seam): typed MediaServerConfig
in, list[MediaItem] out, no DB writes. The reconcile service orchestrates them
and is the only writer."""

from __future__ import annotations

from typing import Optional, Protocol

from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem


class MediaServerAdapter(Protocol):
    config: MediaServerConfig

    def list_items(self, since: Optional[str]) -> list[MediaItem]: ...


def build_adapter(config: MediaServerConfig) -> MediaServerAdapter:
    """Instantiate the adapter for a config's source_type."""
    if config.source_type == "emby":
        from javdb.integrations.media_servers.emby.adapter import EmbyAdapter
        return EmbyAdapter(config)
    if config.source_type == "plex":
        from javdb.integrations.media_servers.plex.adapter import PlexAdapter
        return PlexAdapter(config)
    raise ValueError(f"no adapter for source_type: {config.source_type!r}")
```

- [ ] **Step 4: Write a tiny shared HTTP caller** (token masking + auth header per type)

```python
# javdb/integrations/media_servers/emby/__init__.py
"""Emby media-server adapter package."""
```
```python
# javdb/integrations/media_servers/emby/adapter.py
"""Emby adapter: REST + X-Emby-Token → raw MediaItem (ADR-033 D7).

Read-only. Never writes the DB. The service resolves video_codes from the
raw items this returns."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from javdb.infra.masking import mask_full
from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem

logger = logging.getLogger(__name__)
_TIMEOUT = 30


class _EmbyHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"X-Emby-Token": self._token, "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()


class EmbyAdapter:
    def __init__(self, config: MediaServerConfig, *, http: Any = None) -> None:
        self.config = config
        self._http = http or _EmbyHttp(config.base_url, config.token)

    def _resolve_libraries(self) -> list[dict]:
        # TODO-VERIFY: confirm /Library/MediaFolders vs /Library/VirtualFolders
        # (vs /Users/{id}/Views) for library discovery on the operator's Emby build.
        views = self._http.get_json("/Library/MediaFolders")
        items = (views or {}).get("Items", [])
        wanted = set(self.config.libraries)
        if not wanted:
            return items
        return [v for v in items if v.get("Name") in wanted]

    def list_items(self, since: Optional[str]) -> list[MediaItem]:
        out: list[MediaItem] = []
        for lib in self._resolve_libraries():
            lib_id = str(lib.get("Id", ""))
            lib_name = lib.get("Name")
            params = {
                "ParentId": lib_id,
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "Fields": "Path,UserData",
            }
            data = self._http.get_json("/Items", params=params)
            for raw in (data or {}).get("Items", []):
                ud = raw.get("UserData") or {}
                pct = ud.get("PlayedPercentage")
                out.append(MediaItem(
                    instance=self.config.instance,
                    source_type="emby",
                    library_id=lib_id,
                    library_name=lib_name,
                    item_id=str(raw.get("Id", "")),
                    file_path=raw.get("Path"),
                    folder_name=None,
                    title=raw.get("Name"),
                    watched=bool(ud.get("Played")) if "Played" in ud else None,
                    progress_pct=int(pct) if pct is not None else None,
                    play_count=ud.get("PlayCount"),
                    rating=None,
                    watched_at=ud.get("LastPlayedDate"),
                ))
        logger.info("Emby %s: collected %d items", self.config.instance, len(out))
        return out
```

> **Verify Emby endpoints against your server version** (TODO-VERIFY: confirm
> `/Library/MediaFolders` vs `/Users/{id}/Views` for library discovery and the
> `UserData.Played/PlayCount/PlayedPercentage` field names on the operator's Emby
> build before shipping; the adapter is unit-tested against the **shape** above
> via the injected fake, but the live endpoint path is environment-specific).

```python
# javdb/integrations/media_servers/plex/__init__.py
"""Plex media-server adapter package."""
```
```python
# javdb/integrations/media_servers/plex/adapter.py
"""Plex adapter: X-Plex-Token → raw MediaItem (ADR-033 D7). Read-only."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem

logger = logging.getLogger(__name__)
_TIMEOUT = 30


class _PlexHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"X-Plex-Token": self._token, "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()


def _first_file_path(raw: dict) -> Optional[str]:
    for media in raw.get("Media", []) or []:
        for part in media.get("Part", []) or []:
            if part.get("file"):
                return part["file"]
    return None


class PlexAdapter:
    def __init__(self, config: MediaServerConfig, *, http: Any = None) -> None:
        self.config = config
        self._http = http or _PlexHttp(config.base_url, config.token)

    def _resolve_sections(self) -> list[dict]:
        data = self._http.get_json("/library/sections")
        dirs = ((data or {}).get("MediaContainer") or {}).get("Directory", [])
        wanted = set(self.config.libraries)
        if not wanted:
            return dirs
        return [d for d in dirs if d.get("title") in wanted]

    def list_items(self, since: Optional[str]) -> list[MediaItem]:
        out: list[MediaItem] = []
        for section in self._resolve_sections():
            key = str(section.get("key", ""))
            name = section.get("title")
            data = self._http.get_json(f"/library/sections/{key}/all", params={"type": "1"})
            for raw in ((data or {}).get("MediaContainer") or {}).get("Metadata", []):
                view_count = raw.get("viewCount")
                duration = raw.get("duration") or 0
                offset = raw.get("viewOffset") or 0
                pct = int(offset * 100 / duration) if duration else (100 if view_count else None)
                rating = raw.get("userRating")
                out.append(MediaItem(
                    instance=self.config.instance,
                    source_type="plex",
                    library_id=key,
                    library_name=name,
                    item_id=str(raw.get("ratingKey", "")),
                    file_path=_first_file_path(raw),
                    folder_name=None,
                    title=raw.get("title"),
                    watched=bool(view_count) if view_count is not None else None,
                    progress_pct=pct,
                    play_count=view_count,
                    rating=float(rating) if rating is not None else None,
                    watched_at=str(raw["lastViewedAt"]) if raw.get("lastViewedAt") else None,
                ))
        logger.info("Plex %s: collected %d items", self.config.instance, len(out))
        return out
```

> **Verify Plex field names** (TODO-VERIFY: confirm `viewCount`/`viewOffset`/
> `duration`/`userRating`/`lastViewedAt` against the operator's Plex server JSON;
> the unit test pins the shape, the live field names are environment-specific).

- [ ] **Step 5: Run tests to verify they pass** (adjust the Emby/Plex fakes if you change discovery endpoints; keep the `MediaItem` assertions stable)

Run: `pytest tests/unit/test_emby_adapter.py tests/unit/test_plex_adapter.py -v`
Expected: PASS. If a fake's URL matcher doesn't line up with the final endpoint paths, tighten the fake — the adapters' output contract (the `MediaItem` fields) must not change.

- [ ] **Step 6: Commit**

```bash
git add javdb/integrations/media_servers tests/unit/test_emby_adapter.py tests/unit/test_plex_adapter.py
git commit -m "feat(media-servers): add MediaServerAdapter + Emby/Plex adapters (ADR-033 D7)"
```

---

## Task 8: `run_consumption` service (sole writer; per-instance fail-open)

**Files:**
- Modify: `javdb/ops/reconcile/collectors.py` (add `MediaServerCollector`)
- Modify: `javdb/ops/reconcile/service.py` (add `run_consumption`)
- Modify: `javdb/ops/reconcile/__init__.py` (re-export)
- Test: `tests/unit/test_reconcile_consumption_service.py`

`run_consumption` is the **only writer** of `ConsumptionSignal` and `UnresolvedMediaItem`. It iterates `options.servers`, builds (or accepts injected) adapters, collects raw `MediaItem`s through the read-only `MediaServerCollector`, runs `resolve_video_code`, and UPSERTs. Per ADR-033 D-P3-6 each instance is wrapped in try/except: a failure appends to `result.errors` and continues. Prior signal rows for an unobserved instance are left untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reconcile_consumption_service.py
import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import ConsumptionOptions, MediaItem
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo

_SIGNAL_DDL = """
CREATE TABLE ConsumptionSignal (
  video_code TEXT NOT NULL, source_type TEXT NOT NULL, instance TEXT NOT NULL,
  library_id TEXT NOT NULL, library_name TEXT, watched INTEGER, progress_pct INTEGER,
  play_count INTEGER, rating REAL, watched_at TEXT, resolved_confidence TEXT,
  observed_at TEXT, PRIMARY KEY (video_code, instance, library_id)
);
"""
_UNRESOLVED_DDL = """
CREATE TABLE UnresolvedMediaItem (
  instance TEXT NOT NULL, source_type TEXT, library_id TEXT NOT NULL,
  library_name TEXT, item_id TEXT NOT NULL, raw_title TEXT, file_path TEXT,
  observed_at TEXT, PRIMARY KEY (instance, library_id, item_id)
);
"""


@pytest.fixture
def repos():
    c = sqlite3.connect(":memory:")
    c.executescript(_SIGNAL_DDL + _UNRESOLVED_DDL)
    return ConsumptionSignalRepo(c), UnresolvedMediaItemRepo(c)


class _FakeAdapter:
    def __init__(self, config, items):
        self.config = config
        self._items = items

    def list_items(self, since):
        return self._items


class _BoomAdapter:
    def __init__(self, config):
        self.config = config

    def list_items(self, since):
        raise ConnectionError("401 unauthorized")


def _cfg(instance="plex-home", st="plex"):
    return MediaServerConfig(st, instance, "http://h", "TOKEN", ())


def test_resolved_item_writes_signal(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                     library_name="JAV", item_id="1",
                     file_path="/m/ABC-123/ABC-123.mp4", watched=True, rating=8.0)
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    got = signal_repo.get("ABC-123", "plex-home", "3")
    assert got.watched == 1
    assert got.resolved_confidence == "high"
    assert res.signals_updated == 1
    assert res.resolved_high == 1
    assert res.marked_unresolved == 0


def test_unresolved_item_is_persisted_and_counted(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg("emby-nas", "emby")
    item = MediaItem(instance="emby-nas", source_type="emby", library_id="7",
                     library_name="Misc", item_id="42",
                     file_path="/m/clip.mp4", title="Family Vacation 2024")
    res = service.run_consumption(
        ConsumptionOptions(servers=[cfg]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    assert res.marked_unresolved == 1
    assert res.signals_updated == 0
    assert unresolved_repo.get("emby-nas", "7", "42") is not None


def test_dead_instance_fails_open(repos):
    signal_repo, unresolved_repo = repos
    good = _cfg("plex-home", "plex")
    bad = _cfg("emby-dead", "emby")
    good_item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                          item_id="1", file_path="/m/MIDV-001.mp4", watched=True)
    res = service.run_consumption(
        ConsumptionOptions(servers=[bad, good]),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={good.instance: _FakeAdapter(good, [good_item]),
                  bad.instance: _BoomAdapter(bad)},
    )
    # bad instance recorded an error but did not abort the good one.
    assert any("emby-dead" in e or "401" in e for e in res.errors)
    assert signal_repo.get("MIDV-001", "plex-home", "3") is not None
    assert res.instances_observed == 1   # only the reachable one counted as observed


def test_dry_run_writes_nothing(repos):
    signal_repo, unresolved_repo = repos
    cfg = _cfg()
    item = MediaItem(instance="plex-home", source_type="plex", library_id="3",
                     item_id="1", file_path="/m/ABC-123.mp4", watched=True)
    service.run_consumption(
        ConsumptionOptions(servers=[cfg], dry_run=True),
        repo=signal_repo, unresolved_repo=unresolved_repo,
        adapters={cfg.instance: _FakeAdapter(cfg, [item])},
    )
    assert signal_repo.get("ABC-123", "plex-home", "3") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reconcile_consumption_service.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_consumption'`

- [ ] **Step 3: Add the read-only `MediaServerCollector`** to `javdb/ops/reconcile/collectors.py`

Append (the module already has the `SourceCollector` Protocol + `QbCollector`, collectors.py:27-54):

```python
class MediaServerCollector:
    """Read-only wrapper over a MediaServerAdapter. Never writes the DB."""

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    @property
    def config(self):
        return self._adapter.config

    def collect(self, since):
        return list(self._adapter.list_items(since))
```

- [ ] **Step 4: Add `run_consumption`** to `javdb/ops/reconcile/service.py`

Append after `run()` (service.py:123-194). Reuse the existing `_repo_ctx` helper (service.py:24-31) for the signal repo, and add a second context for the unresolved repo.

```python
import contextlib as _contextlib  # already imported as contextlib at top; reuse it

from javdb.ops.reconcile.code_resolver import resolve_video_code
from javdb.ops.reconcile.collectors import MediaServerCollector
from javdb.ops.reconcile.media_config import parse_media_servers
from javdb.ops.reconcile.models import (
    ConsumptionOptions,
    ConsumptionResult,
    ConsumptionSignalRecord,
    UnresolvedMediaItemRecord,
)
from javdb.ops.reconcile.persistence import open_consumption_repo, open_unresolved_repo


@contextlib.contextmanager
def _consumption_repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_consumption_repo() as opened:
            yield opened


@contextlib.contextmanager
def _unresolved_repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_unresolved_repo() as opened:
            yield opened


_CONFIDENCE_COUNTER = {
    "high": "resolved_high",
    "medium": "resolved_medium",
    "low": "resolved_low",
}


def run_consumption(
    options: ConsumptionOptions,
    *,
    repo=None,
    unresolved_repo=None,
    adapters=None,
) -> ConsumptionResult:
    """Pull watch signal from media servers and UPSERT ConsumptionSignal.

    Sole writer. Per-instance fail-open (ADR-033 D-P3-6): a dead instance logs a
    masked warning, records an error, and is skipped — the pass continues."""
    result = ConsumptionResult()
    servers = list(options.servers)
    if not servers:
        logger.info("run_consumption: no MEDIA_SERVERS configured; nothing to do")
        return result

    now = utc_now_iso()
    with _consumption_repo_ctx(repo) as signal_repo, \
            _unresolved_repo_ctx(unresolved_repo) as unresolved:
        for cfg in servers:
            try:
                adapter = (adapters or {}).get(cfg.instance) if adapters else None
                if adapter is None:
                    from javdb.integrations.media_servers import build_adapter
                    adapter = build_adapter(cfg)
                items = MediaServerCollector(adapter).collect(options.since)
            except Exception as exc:
                logger.warning("run_consumption: instance %s failed (skipping)",
                               cfg.instance, exc_info=True)
                result.errors.append(f"{cfg.instance}: {exc}")
                continue

            result.instances_observed += 1
            result.items_observed += len(items)
            for item in items:
                video_code, confidence = resolve_video_code(item)
                if video_code is None:
                    result.marked_unresolved += 1
                    if not options.dry_run:
                        unresolved.upsert(UnresolvedMediaItemRecord(
                            instance=item.instance,
                            source_type=item.source_type,
                            library_id=item.library_id,
                            library_name=item.library_name,
                            item_id=item.item_id,
                            raw_title=item.title,
                            file_path=item.file_path,
                            observed_at=now,
                        ))
                    continue

                counter = _CONFIDENCE_COUNTER.get(confidence)
                if counter:
                    setattr(result, counter, getattr(result, counter) + 1)
                if options.dry_run:
                    continue
                signal_repo.upsert(ConsumptionSignalRecord(
                    video_code=video_code,
                    source_type=item.source_type,
                    instance=item.instance,
                    library_id=item.library_id,
                    library_name=item.library_name,
                    watched=item.watched,
                    progress_pct=item.progress_pct,
                    play_count=item.play_count,
                    rating=item.rating,
                    watched_at=item.watched_at,
                    resolved_confidence=confidence,
                    observed_at=now,
                ))
                result.signals_updated += 1
    return result
```

> **Note on `instances_observed` vs `errors`.** `instances_observed` counts only
> instances that returned items without raising (the dead instance in
> `test_dead_instance_fails_open` is *not* counted). This makes "observed" mean
> "successfully read", which is what the cron summary and the no-silent-caps audit
> want to see.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_reconcile_consumption_service.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Re-export from the package `__init__`**

Append to `javdb/ops/reconcile/__init__.py` (it already re-exports Phase-1 symbols, __init__.py:3-27 — add to both the import block and `__all__`):

```python
from .models import ConsumptionOptions, ConsumptionResult, MediaItem  # noqa: F401
from .service import run_consumption  # noqa: F401
```
Add `"ConsumptionOptions"`, `"ConsumptionResult"`, `"MediaItem"`, `"run_consumption"` to `__all__`.

- [ ] **Step 7: Commit**

```bash
git add javdb/ops/reconcile/collectors.py javdb/ops/reconcile/service.py \
       javdb/ops/reconcile/__init__.py tests/unit/test_reconcile_consumption_service.py
git commit -m "feat(reconcile): add run_consumption service (ADR-033 Phase 3)"
```

---

## Task 9: CLI — wire the `--pass consumption` arm

**Files:**
- Modify: `apps/cli/ops/reconcile.py`
- Test: `tests/smoke/test_reconcile_consumption_cli.py`

> **Ownership split (do not re-introduce `--pass`).** Phase 2 (IMP-ADR033-02 Task 9)
> **introduces** the `--pass` selector with choices `{acquisition, ownership, all}`
> ONLY — the selector itself is Phase-2-owned. Phase 3 (this task) **extends** that
> same selector: it adds `consumption` to the argparse `choices` tuple, wires the
> consumption branch, and grows the meaning of `all` to also include consumption.
> It does **not** re-create the selector.
>
> **[CRITICAL — execution order]** Do not start this task until Phase 2 Task 9 has
> landed and `apps/cli/ops/reconcile.py` already defines `--pass` with
> `dest="pass_name"`, `choices=("acquisition","ownership","all")`, `default="all"`.
> If the selector does not yet exist, STOP and coordinate with the Phase-2 worker
> rather than re-adding it here.

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_reconcile_consumption_cli.py
import subprocess
import sys


def test_reconcile_pass_consumption_dry_run_is_sane():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile",
         "--pass", "consumption", "--dry-run", "--json", "--log-level", "WARNING"],
        capture_output=True, text=True,
    )
    # No MEDIA_SERVERS configured in CI → empty pass, exit 0 (or 2 if an
    # instance error was captured). A traceback / non-{0,2} exit is a failure.
    assert r.returncode in (0, 2), r.stderr


def test_reconcile_help_mentions_consumption():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "consumption" in r.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_reconcile_consumption_cli.py -v`
Expected: FAIL — `--help` does not mention `consumption` yet (and/or `--pass consumption` is unhandled).

- [ ] **Step 3: Extend the `--pass` choices and wire the consumption arm**

In `apps/cli/ops/reconcile.py` (the current parser is at reconcile.py:56-96; `main` is at 99-141). Phase 2 added `--pass` with `dest="pass_name"`, `choices=("acquisition","ownership","all")`, `default="all"`. Phase 3 first **extends that `choices` tuple** to include `consumption`, then adds a matching dispatch branch (mirroring Phase 2's inline `pass_name in (...)` branches — do **not** restructure into a set/dispatch table):

```python
# In _build_parser(): extend the existing --pass choices (Phase 2 defined the arg).
# Change choices=("acquisition","ownership","all")
#     to choices=("acquisition","ownership","consumption","all")
# and update its help to note `all` now also runs the consumption pass.
```

The consumption pass loads + parses `MEDIA_SERVERS` and runs `run_consumption`:

```python
from dataclasses import asdict

from javdb.ops.reconcile.media_config import parse_media_servers
from javdb.ops.reconcile.models import ConsumptionOptions
from javdb.ops.reconcile.service import run_consumption


def _run_consumption_pass(args) -> int:
    """Build ConsumptionOptions from MEDIA_SERVERS and run the consumption pass."""
    try:
        servers = parse_media_servers(cfg("MEDIA_SERVERS", []))
    except ValueError as exc:
        print(f"Error: invalid MEDIA_SERVERS config: {exc}", file=sys.stderr)
        return 1
    options = ConsumptionOptions(servers=servers, dry_run=args.dry_run)
    result = run_consumption(options)
    if args.json_output:
        print(json.dumps(asdict(result), ensure_ascii=False))
    else:
        log_section(logger, "Consumption Signal Reconcile")
        log_summary_block(logger, "Consumption Summary", {
            "Instances observed": result.instances_observed,
            "Items observed": result.items_observed,
            "Signals updated": result.signals_updated,
            "Resolved high/medium/low": f"{result.resolved_high}/"
                                        f"{result.resolved_medium}/{result.resolved_low}",
            "Marked unresolved": result.marked_unresolved,
            "Errors": len(result.errors),
        })
    return 2 if result.errors else 0
```

Then, in `main`, add the consumption branch alongside Phase 2's existing
`pass_name in (...)` branches (Phase 2 inlines `acquisition`/`ownership`; Phase 3
adds `consumption`, and `all` now fans into all three). Collect each branch's exit
code and return the max:

```python
        exit_codes = []
        if args.pass_name in ("acquisition", "all"):
            exit_codes.append(_run_acquisition_exit(args))   # Phase 1/2 inline path
        if args.pass_name in ("ownership", "all"):
            exit_codes.append(_run_ownership_exit(args))     # Phase 2 inline path
        if args.pass_name in ("consumption", "all"):         # Phase 3 (this task)
            exit_codes.append(_run_consumption_pass(args))
        return max(exit_codes) if exit_codes else 0
```

> `_run_acquisition_exit` / `_run_ownership_exit` are shorthand for whatever Phase 2
> left inline for those two branches — do **not** restructure them. Phase 3 only
> appends the `consumption` branch and its `_run_consumption_pass` helper, matching
> Phase 2's `args.pass_name in (...)` shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/smoke/test_reconcile_consumption_cli.py -v`
Expected: PASS (2 passed). `--pass consumption --dry-run` with no `MEDIA_SERVERS` returns an empty `ConsumptionResult` and exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/cli/ops/reconcile.py tests/smoke/test_reconcile_consumption_cli.py
git commit -m "feat(reconcile): wire --pass consumption CLI arm (ADR-033 Phase 3)"
```

---

## Task 10: Workflow + config example

**Files:**
- Modify: `.github/workflows/ReconcileLibrary.yml`
- Modify: `config.py.example`

`ReconcileLibrary.yml` already runs `python3 -m apps.cli.ops.reconcile ... --json` and (after Phase 2) passes `--pass all`, so consumption is **already executed** by the cron — Phase 3 only needs to surface the `MEDIA_SERVERS` secret/var to `config_generator`. The workflow generates `config.py` from `VAR_*` env vars via `apps.cli.ops.config_generator --github-actions` (ReconcileLibrary.yml:67-84).

- [ ] **Step 1: Add the `MEDIA_SERVERS` config-generation note**

In the "Generate config.py from GitHub Variables and Secrets" step (ReconcileLibrary.yml:67-84), add the media-server secret alongside the qB block. `MEDIA_SERVERS` is a structured list, so it is supplied as a JSON-encoded secret that `config_generator` materializes:

```yaml
          # ADR-033 Phase 3: media servers for the consumption pass (--pass all).
          # JSON list of {type,instance,base_url,token,libraries?}; tokens inline.
          VAR_MEDIA_SERVERS_JSON: ${{ secrets.MEDIA_SERVERS_JSON }}
```

> **`config_generator` support (TODO-VERIFY):** confirm whether
> `apps/cli/ops/config_generator.py` already materializes a JSON `VAR_*` into a
> Python literal in `config.py`. If it does not yet handle `MEDIA_SERVERS_JSON`,
> add a minimal mapping there (one line, mirroring how it emits other structured
> values such as `PROXY_POOL`) **in this task** and pin it with a config_generator
> unit test. If the operator only runs the loop locally (config.py on disk), no
> workflow change beyond this note is required.

- [ ] **Step 2: Validate the workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ReconcileLibrary.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Document the `MEDIA_SERVERS` block in `config.py.example`**

Add near the other ops settings (after `RECONCILE_STALLED_DAYS`, config.py.example:212), modeled on the PROXY_POOL list-of-dicts (config.py.example:114-145):

```python
# ADR-033 media closed-loop (Phase 3): media servers polled for watch signal.
# A list of dicts, one per server instance. Multiple servers of the same type
# are first-class (e.g. two Plex). Tokens are inline (config.py is encrypted at
# rest as config.py.enc) and are masked in all logs.
#
#   type      : 'emby' | 'plex'
#   instance  : a unique connection id; flows into ConsumptionSignal.instance
#   base_url  : http(s)://host:port (no trailing slash needed)
#   token     : Emby API key (X-Emby-Token) / Plex token (X-Plex-Token)
#   libraries : OPTIONAL list of human-readable library NAMES to scan;
#               omit to scan ALL libraries on that server.
MEDIA_SERVERS = [
    # {
    #     'type': 'emby',
    #     'instance': 'emby-nas',
    #     'base_url': 'http://192.168.1.50:8096',
    #     'token': 'your_emby_api_key',
    #     'libraries': ['JAV'],
    # },
    # {
    #     'type': 'plex',
    #     'instance': 'plex-home',
    #     'base_url': 'http://192.168.1.51:32400',
    #     'token': 'your_plex_token',
    #     # 'libraries' omitted -> scan all libraries
    # },
]
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ReconcileLibrary.yml config.py.example
git commit -m "feat(ci): surface MEDIA_SERVERS for consumption pass + config example (ADR-033 Phase 3)"
```

---

## Task 11: Docs

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/handbook/en/developer/cli-reference.md` (+ `docs/handbook/zh/developer/cli-reference.md`)
- Create: `docs/handbook/en/self-hoster/media-servers.md` (+ `docs/handbook/zh/self-hoster/media-servers.md`)
- Modify: `docs/handbook/en/self-hoster/github-actions-setup.md` (+ zh)

- [ ] **Step 1: Update `CONTEXT.md`** — the ADR-033 domain terms were added in
  Phase 1 with *Consumption signal* / *Ownership ledger* marked as Phase 2/3.
  Promote *Consumption signal* to live and add the two Phase-3 terms:
  - **Consumption signal** — per `(video_code, instance, library)` watch/rating
    evidence pulled from media servers; the strongest implicit preference signal
    (now live, Phase 3).
  - **Join-key resolution** — best-effort mapping of a media item to a
    `video_code` via the high/medium/low confidence ladder (ADR-033 D9).
  - **Unresolved media item** — a media item whose `video_code` could not be
    resolved; counted and persisted, never silently dropped.

- [ ] **Step 2: Update CLI reference** — extend the existing "Acquisition Reconcile
  CLI" section (cli-reference.md:635, module `apps.cli.ops.reconcile`) to document
  the `--pass consumption` arm: what it does (polls `MEDIA_SERVERS`, resolves codes,
  writes `ConsumptionSignal` + `UnresolvedMediaItem`), that `--pass all` includes it,
  and that it needs `MEDIA_SERVERS` in `config.py`. Mirror into the zh file.

- [ ] **Step 3: Write the self-hoster media-servers page** —
  `docs/handbook/en/self-hoster/media-servers.md`: how to obtain an Emby API key
  and a Plex token, the `MEDIA_SERVERS` config shape (link to `config.py.example`,
  do not duplicate the block — reference it), multi-instance examples, the
  `libraries` name-resolution behaviour, and how the consumption pass runs (via
  `ReconcileLibrary.yml --pass all` or `python3 -m apps.cli.ops.reconcile --pass consumption`).
  Add a TOC if it exceeds ~300 lines. Mirror into the zh file (translate prose +
  comments; never translate code/paths/CLI/SQL).

- [ ] **Step 4: Update GitHub Actions setup** —
  `docs/handbook/en/self-hoster/github-actions-setup.md`: document the
  `MEDIA_SERVERS_JSON` secret used by `ReconcileLibrary.yml` for the consumption
  pass (link to the media-servers page rather than duplicating). Mirror into zh.

- [ ] **Step 5: Commit**

```bash
git add CONTEXT.md docs/handbook
git commit -m "docs(reconcile): document consumption signal + MEDIA_SERVERS (ADR-033 Phase 3)"
```

---

## Task 12: Full-suite verification gate

- [ ] **Step 1: Run the new Phase-3 tests together**

Run:
```bash
pytest tests/unit/test_consumption_models.py \
       tests/unit/test_code_resolver.py \
       tests/unit/test_consumption_signal_repo.py \
       tests/unit/test_unresolved_media_item_repo.py \
       tests/unit/test_media_config.py \
       tests/unit/test_emby_adapter.py \
       tests/unit/test_plex_adapter.py \
       tests/unit/test_reconcile_consumption_service.py \
       tests/smoke/test_reconcile_consumption_cli.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run the broader suite for regressions in touched areas**

Run: `pytest tests/unit -k "reconcile or consumption or media or resolver or acquisition" -q`
Expected: all PASS (no import-time breakage from the modified `models.py` /
`service.py` / `collectors.py` / `persistence.py` / `__init__.py`, and Phase-1
reconcile tests still green).

- [ ] **Step 3: Confirm the seam invariant (collectors + adapters are read-only)**

The adapters and the `MediaServerCollector` must perform **no** DB writes; the
only `upsert` call sites are inside `service.py` and the repos.

Run:
```bash
grep -rnE "upsert|mark_state|INSERT |UPDATE |\.execute\(" \
  javdb/ops/reconcile/collectors.py \
  javdb/ops/reconcile/code_resolver.py \
  javdb/integrations/media_servers/
```
Expected: no output (read-only seam holds). If any line appears, a writer leaked
into a collector/adapter — move it into `service.py`.

- [ ] **Step 4: Confirm token masking holds**

Run:
```bash
python3 -c "from javdb.ops.reconcile.media_config import parse_media_servers; c=parse_media_servers([{'type':'plex','instance':'p','base_url':'http://h','token':'TOPSECRET'}])[0]; assert 'TOPSECRET' not in repr(c), repr(c); print('mask ok')"
```
Expected: `mask ok`

- [ ] **Step 5: Whitespace check + final commit (if any tidy-ups remain)**

```bash
git diff --check
git add -A && git commit -m "test(reconcile): full-suite verification for ADR-033 Phase 3"
```

---

## Plan Self-Review

**Spec coverage (ADR-033 Phase 3 row + D-P3-* + shared decisions):**
- `ConsumptionSignal` + `UnresolvedMediaItem` tables, D1-first + local mirror (D-X-3) → Task 1. ✓
- `MediaItem` / `ConsumptionSignalRecord` / `UnresolvedMediaItemRecord` + confidence constants (shared-module evolution: models append-only) → Task 2. ✓
- Join-key resolver in the **service** layer, high/medium/low ladder, reuse audit + NFKC-normalize (D9 / D-P3-5) → Task 3 (resolver) wired in Task 8 (service). Reuse audit performed: **no general string→code extractor exists**; resolver delegates validity to `javdb.parsing.common._is_plausible_video_code` / `classify_video_code_family`. ✓
- `ConsumptionSignalRepo` + `UnresolvedMediaItemRepo`, idempotent UPSERT mirroring `AcquisitionOutcomeRepo`; ConsumptionSignal full-replace per `(video_code,instance,library_id)` (D-P3-7 / D8) → Task 4. ✓
- `open_consumption_repo` / `open_unresolved_repo`, call-time path resolution (BFR-016) → Task 5. ✓
- `MEDIA_SERVERS` parsing/validation + `mask_full` token masking (D-P3-1) → Task 6. ✓
- `MediaServerAdapter` Protocol + `EmbyAdapter` (X-Emby-Token) + `PlexAdapter` (X-Plex-Token) under `javdb/integrations/media_servers/{emby,plex}/`, returning raw `MediaItem`, read-only (D7 / D-P3-3) → Task 7. ✓
- `run_consumption` service: per-instance fail-open (D-P3-6), resolve, full-replace `ConsumptionSignal` UPSERT, persist + count unresolved (D-P3-4), reads `MEDIA_SERVERS` via `cfg` / injectable adapters (D-P3-2) → Task 8. ✓
- `MediaServerCollector` read-only wrapper in `collectors.py` (shared-module evolution) → Task 8 Step 3, gate Task 12 Step 3. ✓
- CLI `--pass consumption` arm — Phase 3 **adds** the arm; Phase 2 introduced the selector → Task 9 (ownership split stated). ✓
- `ReconcileLibrary.yml` already runs `--pass all` (Phase 2); Phase 3 adds `MEDIA_SERVERS` secret note only → Task 10. ✓
- `config.py.example` `MEDIA_SERVERS` block (PROXY_POOL-style) → Task 10. ✓
- Docs: CONTEXT.md consumption terms + cli-reference (en/zh) + new self-hoster media-servers page (en/zh) + github-actions-setup (en/zh) → Task 11. ✓
- Full-suite gate + read-only-seam grep + token-mask check (mirror IMP-01 Task 11) → Task 12. ✓

**Type consistency:** `MediaItem`, `ConsumptionSignalRecord`, `UnresolvedMediaItemRecord`,
`ConsumptionOptions`, `ConsumptionResult`, `MediaServerConfig`, `resolve_video_code`,
`ConsumptionSignalRepo`/`UnresolvedMediaItemRepo`, `MediaServerAdapter`/`EmbyAdapter`/
`PlexAdapter`/`build_adapter`, `MediaServerCollector`, and `run_consumption` are used
identically across Tasks 2-12. ✓

**Shared-module agreement with the sibling (IMP-ADR033-02):**
- `models.py` — Phase 3 only **appends** `MediaItem` + consumption records/options/results + `RESOLVED_CONFIDENCES`; never rewrites Phase-1/2 models. ✓
- `service.py` — Phase 3 adds `run_consumption` next to `run()` (Phase 1) and `run_ownership` (Phase 2); existing `run()` untouched. ✓
- `persistence.py` — Phase 3 adds `open_consumption_repo` / `open_unresolved_repo` next to `open_outcome_repo` (Phase 1) and `open_ledger_repo` (Phase 2). ✓
- `collectors.py` — Phase 3 adds `MediaServerCollector`; media HTTP adapters live in `integrations/media_servers/`. ✓
- `__init__.py` — Phase 3 appends its re-exports; does not remove Phase-1/2 ones. ✓
- CLI `--pass` selector — Phase 2 introduces it; Phase 3 adds the `consumption` value only. ✓
- `ReconcileLibrary.yml` — Phase 2 sets `--pass all`; Phase 3 adds only `MEDIA_SERVERS` config notes. ✓

**Known couplings / risks (documented, intentional):**
- Emby/Plex live endpoint paths + field names are environment-specific (Task 7 TODO-VERIFY notes). Unit tests pin the `MediaItem` output contract via injected fakes, but the operator must confirm the real endpoints against their server build before the first live run.
- `config_generator` JSON-`VAR_*` materialization for `MEDIA_SERVERS_JSON` is a TODO-VERIFY in Task 10 Step 1 — confirm/extend it before relying on the CI path; the local-config path needs no workflow change.

**Deferred / out of scope (explicit):**
- **Web surface** (read endpoints, dual-backend parity, Vue view) is tracked by **ADR-034 Phase 3** and is out of scope here (D-X-1). No dual-backend parity gate in this IMP.
- **`closed_loop` capability flag** granularity is ADR-034's concern (D-X-2).
- **Preference model** consuming `ConsumptionSignal` is ADR-025's concern (ADR-033 D11) — Phase 3 produces the signal and stops.

**Open verification dependency:** Task 1 Steps 2-3 require live `wrangler` D1
access and `apps.cli.db.sync_d1_to_sqlite`; run them in an environment with D1
credentials (the same place other migrations are applied). These are
deployment-environment gates, exactly as IMP-ADR033-01 Task 1 Steps 2-3.
