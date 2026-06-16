"""ADR-055: contract registry primitives."""
import pytest

from javdb.storage.contract.types import (
    Param,
    SharedConstant,
    SqlFragment,
    normalize_sql,
    order_params,
)

_F = SqlFragment(
    name="demo",
    db="history",
    sql="INSERT INTO T (a, b) VALUES (?, ?)",
    params=(Param("a", "str", "string"), Param("b", "str | None", "string | null")),
)


def test_normalize_collapses_whitespace():
    assert normalize_sql("  a\n   b\t c ") == "a b c"


def test_order_params_orders_by_registry_not_call_site():
    # kwargs given out of order -> tuple still follows fragment param order.
    assert order_params(_F, b="bee", a="aye") == ("aye", "bee")


def test_order_params_allows_param_named_fragment():
    fragment = SqlFragment(
        name="fragment_param",
        db="history",
        sql="SELECT ?",
        params=(Param("fragment", "str", "string"),),
    )

    assert order_params(fragment, fragment="ok") == ("ok",)


def test_order_params_rejects_missing_and_extra():
    with pytest.raises(ValueError, match=r"order_params\(demo\):.*missing=\['b'\]"):
        order_params(_F, a="aye")  # missing b
    with pytest.raises(ValueError, match=r"order_params\(demo\):.*extra=\['c'\]"):
        order_params(_F, a="aye", b="bee", c="nope")  # extra c


def test_shared_constant_declares_static_cross_backend_values():
    literal = SharedConstant(
        name="demo_literal",
        kind="string",
        values="prefix_",
    )
    constant = SharedConstant(
        name="demo_values",
        kind="string_set",
        values=("alpha", "beta"),
    )
    numeric = SharedConstant(
        name="demo_number",
        kind="number",
        values=24,
    )

    assert literal.name == "demo_literal"
    assert literal.kind == "string"
    assert literal.values == "prefix_"
    assert numeric.name == "demo_number"
    assert numeric.kind == "number"
    assert numeric.values == 24
    assert constant.name == "demo_values"
    assert constant.kind == "string_set"
    assert constant.values == ("alpha", "beta")
