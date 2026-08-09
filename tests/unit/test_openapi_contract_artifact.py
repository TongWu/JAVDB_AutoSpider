"""Guard the committed OpenAPI artifact against duplicate-key corruption.

``apps/cli/ops/dump_openapi.py`` serializes the schema from a Python dict via
``json.dumps(..., sort_keys=True)``, so a freshly generated artifact can never
contain duplicate object keys. They only appear when the file is hand-edited or
when a careless merge concatenates two regenerations — which is exactly what
produced the malformed ``/api/onboarding/test`` entry (401/403 misplaced inside
``requestBody``) and the duplicate ``403``/``422`` blocks this guard now pins.

A duplicate key parses (``json.load`` keeps the last value) but silently drops
the others, so the corruption is invisible to a plain ``json.load`` round-trip
and to consumers — until a strict parser or SDK generator chokes on it. This
test reads the raw bytes and fails on any duplicate key anywhere in the tree.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI = _REPO_ROOT / "docs" / "api" / "openapi.json"


def _duplicate_keys(pairs):
    seen: set[str] = set()
    dups: list[str] = []
    for key, _ in pairs:
        if key in seen:
            dups.append(key)
        seen.add(key)
    return dups


def test_committed_openapi_has_no_duplicate_keys():
    offenders: list[list[str]] = []

    def hook(pairs):
        dups = _duplicate_keys(pairs)
        if dups:
            offenders.append(dups)
        return dict(pairs)

    json.loads(_OPENAPI.read_text(encoding="utf-8"), object_pairs_hook=hook)
    assert offenders == [], (
        "docs/api/openapi.json has duplicate object keys (regenerate via "
        f"`python -m apps.cli.ops.dump_openapi`): {offenders}"
    )


def test_onboarding_test_path_is_single_and_well_formed():
    schema = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    op = schema["paths"]["/api/onboarding/test"]["post"]
    # The auth-failure bodies belong under responses, never under requestBody.
    assert set(op["requestBody"].keys()) == {"content", "required"}
    assert {"401", "403"}.issubset(op["responses"].keys())


def test_aggregate_magnets_response_requires_magnets_array():
    schema = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    response_schema = schema["components"]["schemas"]["AggregateMagnetsResponse"]
    assert set(response_schema["required"]) == {"video_code", "magnets"}
