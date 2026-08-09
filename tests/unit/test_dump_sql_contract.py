"""ADR-055: the generator renders a deterministic, idiomatic TS mirror."""
import re

import pytest

from apps.cli.ops.dump_sql_contract import _render_constant, _render_fragment, render
from javdb.storage.contract.types import SharedConstant, SqlFragment


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
    assert "export function prepareActorSubscriptionUpsert(" in out
    assert "export function prepareSystemStateUpsert(" in out
    assert "export const VALID_RULE_MODES: ReadonlySet<string> = new Set([" in out
    assert "export const VALUE_REQUIRED: ReadonlySet<string> = new Set([" in out
    assert "export const REPORT_SESSION_COLUMNS: readonly string[] = [" in out


def test_render_fragment_escapes_template_literal_control_sequences():
    fragment = SqlFragment(
        name="literal_escape",
        db="reports",
        sql="SELECT '`' AS backtick, '${unsafe}' AS interpolation",
        params=(),
    )

    out = _render_fragment(fragment)

    assert r"SELECT '\`' AS backtick, '\${unsafe}' AS interpolation" in out


def test_render_fragment_handles_zero_param_fragments_without_unused_param():
    fragment = SqlFragment(
        name="list_all_things",
        db="reports",
        sql="SELECT * FROM Things",
        params=(),
    )

    out = _render_fragment(fragment)

    assert "_p?: Record<string, never>" in out
    assert "return db.prepare(LIST_ALL_THINGS_SQL).bind();" in out


def test_render_constant_supports_sets_and_arrays():
    assert (
        _render_constant(
            SharedConstant(name="policy_id_prefix", kind="string", values="opspolicy_")
        )
        == 'export const POLICY_ID_PREFIX = "opspolicy_";\n'
    )
    assert (
        _render_constant(
            SharedConstant(name="hash_length", kind="number", values=24)
        )
        == "export const HASH_LENGTH = 24;\n"
    )
    assert (
        _render_constant(
            SharedConstant(name="valid_modes", kind="string_set", values=("a", "b"))
        )
        == 'export const VALID_MODES: ReadonlySet<string> = new Set([\n'
        '  "a",\n'
        '  "b",\n'
        "]);\n"
    )
    assert (
        _render_constant(
            SharedConstant(name="columns", kind="string_array", values=("Id",))
        )
        == 'export const COLUMNS: readonly string[] = [\n'
        '  "Id",\n'
        "];\n"
    )


def test_render_constant_escapes_string_tuple_values():
    assert (
        _render_constant(
            SharedConstant(
                name="columns",
                kind="string_array",
                values=('Quote"Column', r"path\to\column"),
            )
        )
        == 'export const COLUMNS: readonly string[] = [\n'
        '  "Quote\\"Column",\n'
        '  "path\\\\to\\\\column",\n'
        "];\n"
    )


def test_render_constant_rejects_bool_number_value():
    with pytest.raises(TypeError, match="number constant requires an int value"):
        _render_constant(SharedConstant(name="bad_number", kind="number", values=True))


def test_render_constant_rejects_non_string_tuple_values():
    with pytest.raises(TypeError, match="string_array constant requires string tuple values"):
        _render_constant(SharedConstant(name="bad_array", kind="string_array", values=("ok", 1)))
