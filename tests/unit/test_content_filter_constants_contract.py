"""ADR-055 D8: content-filter allow-lists come from the registry + wire into the router."""
from apps.api.routers import content_filter as cf_router
from apps.cli.ops.content_filter import VALID_RULE_MODES, VALUE_REQUIRED


def test_allow_lists_use_encoded_string_keys():
    assert "actor:exclude" in VALID_RULE_MODES
    assert "gender:exclude_all_male" in VALID_RULE_MODES
    assert "gender:exclude_all_male" not in VALUE_REQUIRED
    assert "release_date:after" in VALUE_REQUIRED


def test_router_shares_the_same_allow_list_objects():
    # No second copy: the router consumes the CLI module's registry-derived sets.
    assert cf_router.VALID_RULE_MODES is VALID_RULE_MODES
    assert cf_router.VALUE_REQUIRED is VALUE_REQUIRED
