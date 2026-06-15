"""ADR-055: the generator renders a deterministic, idiomatic TS mirror."""
import re

from apps.cli.ops.dump_sql_contract import _render_fragment, render
from javdb.storage.contract.types import SqlFragment


def test_render_is_deterministic():
    assert render() == render()


def test_render_emits_const_and_typed_prepare_helper():
    out = render()
    assert "// AUTO-GENERATED" in out
    assert re.search(r"^// version: [0-9a-f]{16}$", out, re.MULTILINE)
    assert "export const WATCH_INTENT_UPSERT_SQL =" in out
    # typed bind helper: snake_case params -> camelCase object keys, registry order
    assert "export function prepareWatchIntentUpsert(" in out
    assert "db: D1Database," in out
    assert "videoCode: string; href: string; status: string; notes: string | null" in out
    assert "): D1PreparedStatement {" in out
    assert (
        "return db.prepare(WATCH_INTENT_UPSERT_SQL).bind(p.videoCode, p.href, p.status, p.notes);"
        in out
    )


def test_render_fragment_escapes_template_literal_control_sequences():
    fragment = SqlFragment(
        name="literal_escape",
        db="reports",
        sql="SELECT '`' AS backtick, '${unsafe}' AS interpolation",
        params=(),
    )

    out = _render_fragment(fragment)

    assert r"SELECT '\`' AS backtick, '\${unsafe}' AS interpolation" in out
