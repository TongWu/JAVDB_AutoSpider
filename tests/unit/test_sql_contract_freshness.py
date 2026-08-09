"""ADR-055: the committed TS mirror must match the registry (freshness guard)."""
from pathlib import Path

from apps.cli.ops.dump_sql_contract import OUT, render


def test_committed_artifact_is_fresh():
    committed = Path(OUT).read_text(encoding="utf-8")
    assert committed == render(), (
        "sql-contract.gen.ts is stale — run: "
        "python3 -m apps.cli.ops.dump_sql_contract and commit the result"
    )
