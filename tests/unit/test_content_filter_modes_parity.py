"""Pin the content-filter (dimension, mode) allow-list so it cannot drift from
the TS Worker (ADR-040 WS4a / WS4-D3). The canonical set tracks the CLI tuples
(the single source of truth) — which include the regex/release_date pairs added
by IMP-ADR040-03. If this fails, fix whichever side drifted, never the test."""

from apps.cli.ops.content_filter import VALID_RULE_MODES, VALUE_REQUIRED
from apps.api.routers import content_filter as cf_router

CANONICAL_VALID = {
    "actor:exclude",
    "tag:exclude",
    "tag:include",
    "gender:require_lead",
    "gender:exclude_all_male",
    "age:min_age",
    "age:max_age",
    "actor:regex_exclude",
    "actor:regex_include",
    "tag:regex_exclude",
    "tag:regex_include",
    "release_date:before",
    "release_date:after",
}
CANONICAL_VALUE_REQUIRED = {
    "actor:exclude",
    "tag:exclude",
    "tag:include",
    "gender:require_lead",
    "age:min_age",
    "age:max_age",
    "actor:regex_exclude",
    "actor:regex_include",
    "tag:regex_exclude",
    "tag:regex_include",
    "release_date:before",
    "release_date:after",
}


def _encode(pairs) -> set:
    return {f"{dim}:{mode}" for (dim, mode) in pairs}


def test_cli_allow_list_matches_canonical():
    assert _encode(VALID_RULE_MODES) == CANONICAL_VALID
    assert _encode(VALUE_REQUIRED) == CANONICAL_VALUE_REQUIRED


def test_router_imports_the_canonical_cli_tuples():
    # The router must use the CLI tuples verbatim (not a copy) so it cannot drift.
    assert cf_router.VALID_RULE_MODES is VALID_RULE_MODES
    assert cf_router.VALUE_REQUIRED is VALUE_REQUIRED
