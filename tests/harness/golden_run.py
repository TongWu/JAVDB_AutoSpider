"""Persist / load committed golden-run snapshots (ADR-037 D7, Phase 3).

A golden run lives at scenarios/golden_runs/<name>/snapshot.json. Snapshots are
written sorted-keys + indented so a behaviour change shows up as a small, legible
diff in review."""

from __future__ import annotations

import json
import os

_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "scenarios", "golden_runs")


def golden_path(name: str) -> str:
    return os.path.join(_GOLDEN_DIR, name, "snapshot.json")


def save_golden(name: str, snapshot: dict) -> None:
    path = golden_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def load_golden(name: str) -> dict:
    with open(golden_path(name), "r", encoding="utf-8") as f:
        return json.load(f)
