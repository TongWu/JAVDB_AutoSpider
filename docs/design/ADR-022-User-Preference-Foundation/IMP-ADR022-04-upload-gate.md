# ADR-022 Phase 4 — B2 Upload Filter Hook

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a replaceable preference gate into the qBittorrent upload path that skips movies whose lead actor is explicitly blocked (`hearted=0` in `ContentPreferences`). The gate is disabled by default via a config flag.

**Architecture:** A single `_preference_gate_blocks(torrent: dict) -> bool` function is added to `uploader.py`. It is called once per torrent in `read_csv_file()`, after the existing `is_downloaded_torrent()` check. The function is designed as a replaceable hook: ADR-B swaps in a model-score-based implementation without further refactoring. The gate fails open on any exception (returns `False`) so a DB error never blocks uploads.

**Tech Stack:** Python 3.11, `javdb.infra.config.cfg`, `PreferenceRepo`.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-03](IMP-ADR022-03-preference-repo.md) · [IMP-ADR022-07](IMP-ADR022-07-tests.md)

**Depends on:** IMP-ADR022-03 (`PreferenceRepo.is_actor_blocked` must exist).

**Blocks:** Nothing — this phase is a leaf.

---

## Task 1 — Add PREFERENCE_GATE_ENABLED config flag

**Files:**
- Modify: `config.py.example`

- [ ] **Step 1: Add the flag**

Open `config.py.example`. Find the section that contains other boolean feature flags (e.g. `CF_BYPASS_ENABLED`, `AUTO_START`). Add after those flags:

```python
# ADR-022: Skip qBittorrent upload for movies whose lead actor is explicitly
# disliked in ContentPreferences (hearted=False). Disabled by default until
# enough preference data has been collected. ADR-B replaces the rule with a
# model-score-based gate.
PREFERENCE_GATE_ENABLED = False
```

- [ ] **Step 2: Commit**

```bash
git add config.py.example
git commit -m "chore(config): add PREFERENCE_GATE_ENABLED flag (ADR-022)"
```

---

## Task 2 — Add gate function to uploader

**Files:**
- Modify: `javdb/integrations/qb/uploader.py`

- [ ] **Step 1: Add `_preference_gate_blocks` function**

Open `javdb/integrations/qb/uploader.py`. Locate the import block at the top and the `logger` definition. After the logger definition and before the first public function, add:

```python
def _preference_gate_blocks(torrent: dict) -> bool:
    """Return True if the preference gate should block this torrent upload.

    Disabled by default (PREFERENCE_GATE_ENABLED = False in config.py).
    When enabled, blocks upload if the movie's lead actor has an explicit
    hearted=0 entry in ContentPreferences.

    Fails open: any exception returns False so a DB error never blocks uploads.

    This is the ADR-022 rule-based placeholder. ADR-B replaces the body of
    this function with a model-score-based gate without changing its signature.
    """
    from javdb.infra.config import cfg
    if not cfg('PREFERENCE_GATE_ENABLED', False):
        return False
    actor_href = torrent.get('actor_link', '')
    if not actor_href:
        return False
    try:
        from javdb.storage.repos.preference_repo import PreferenceRepo
        return PreferenceRepo().is_actor_blocked(actor_href)
    except Exception:
        logger.debug(
            "Preference gate check failed — failing open", exc_info=True
        )
        return False
```

- [ ] **Step 2: Call the gate in `read_csv_file()`**

In `read_csv_file()`, locate the `is_downloaded_torrent(magnet)` check (inside the `for col, label, ttype in torrent_columns:` loop). After that check's `continue` block and before `torrents.append(...)`, add:

```python
                    if _preference_gate_blocks({
                        'actor_link': row.get('actor_link', ''),
                    }):
                        logger.debug(
                            "Preference gate blocked: %s [%s]", video_code, label
                        )
                        skipped_count += 1
                        continue
```

The full loop body should now read, in order:
1. `if not raw: continue`
2. `if not magnet: continue`
3. `if is_downloaded_torrent(magnet): ... continue`
4. `if _preference_gate_blocks(...): ... continue`
5. `torrents.append({...})`

- [ ] **Step 3: Verify gate is inactive by default**

```bash
python3 -c "
from javdb.integrations.qb.uploader import _preference_gate_blocks
result = _preference_gate_blocks({'actor_link': '/actors/anyone'})
assert result is False, f'Expected False, got {result}'
print('Gate correctly inactive when PREFERENCE_GATE_ENABLED=False')
"
```

Expected: `Gate correctly inactive when PREFERENCE_GATE_ENABLED=False`

- [ ] **Step 4: Verify uploader dry-run still works**

```bash
python3 -m apps.cli.qb.uploader --mode adhoc --dry-run 2>&1 | tail -5
```

Expected: completes without errors; no preference-gate-related output.

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/qb/uploader.py
git commit -m "feat(qb): add B2 preference gate hook to upload path (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | `PREFERENCE_GATE_ENABLED` in `config.py.example` | `grep PREFERENCE_GATE_ENABLED config.py.example` → one match |
| 2 | Gate returns False when disabled | `python3 -c "from javdb.integrations.qb.uploader import _preference_gate_blocks; assert _preference_gate_blocks({'actor_link':'/x'}) is False"` → no error |
| 3 | Uploader dry-run unchanged | `python3 -m apps.cli.qb.uploader --mode adhoc --dry-run` → no errors |
| 4 | Gate unit tests pass | `pytest tests/unit/test_preference_gate.py -v` → all PASS (written in IMP-ADR022-07) |
