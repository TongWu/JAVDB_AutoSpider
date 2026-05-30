"""ADR-018 Phase 1: pin Python query builders to the committed Contract Golden."""
import json
from pathlib import Path

import pytest

from apps.cli.ops.dump_query_contract import OUT as GOLDEN_PATH
from apps.cli.ops.dump_query_contract import _BUILDERS, _run_case

GOLDEN = json.loads(Path(GOLDEN_PATH).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "case", GOLDEN["cases"], ids=lambda c: f'{c["builder"]}:{c["name"]}'
)
def test_builder_matches_golden(case):
    sql, bindings, _ = _run_case(case["builder"], case["params"])
    assert sql == case["sql"], f'SQL drift in {case["builder"]}:{case["name"]}'
    assert bindings == case["bindings"], f'bindings drift in {case["builder"]}:{case["name"]}'


def test_golden_covers_all_builders():
    seen = {c["builder"] for c in GOLDEN["cases"]}
    assert seen == set(_BUILDERS), f"golden missing builders: {set(_BUILDERS) - seen}"
