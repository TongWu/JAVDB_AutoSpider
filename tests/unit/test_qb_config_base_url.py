"""Unit tests for the shared qB base-url negotiation helpers.

`ordered_qb_base_urls` and `activate_qb_base_url` were lifted out of two
verbatim copies in the uploader and file_filter services (each mutating its own
module globals) into config.py. Pinning them here makes the negotiation policy
testable without the module-global state that previously trapped it.
"""

from javdb.integrations.qb.config import (
    activate_qb_base_url,
    ordered_qb_base_urls,
)


class TestOrderedQbBaseUrls:
    def test_current_is_tried_first(self):
        candidates = ["https://a:8080", "http://a:8080"]
        assert ordered_qb_base_urls(candidates, "http://a:8080") == [
            "http://a:8080",
            "https://a:8080",
        ]

    def test_no_current_returns_candidates_in_order(self):
        candidates = ["https://a:8080", "http://a:8080"]
        assert ordered_qb_base_urls(candidates, None) == candidates

    def test_current_not_in_candidates_is_prepended(self):
        candidates = ["https://a:8080"]
        assert ordered_qb_base_urls(candidates, "http://b:9090") == [
            "http://b:9090",
            "https://a:8080",
        ]

    def test_no_duplicates_when_current_already_present(self):
        candidates = ["https://a:8080", "http://a:8080"]
        result = ordered_qb_base_urls(candidates, "https://a:8080")
        assert result == ["https://a:8080", "http://a:8080"]
        assert len(result) == len(set(result))


class TestActivateQbBaseUrl:
    def test_https_does_not_force_insecure(self):
        base, masked, allow = activate_qb_base_url("https://qb.internal:8080", False)
        assert base == "https://qb.internal:8080"
        assert allow is False
        assert isinstance(masked, str) and masked

    def test_http_forces_insecure(self):
        base, masked, allow = activate_qb_base_url("http://qb.internal:8080", False)
        assert base == "http://qb.internal:8080"
        assert allow is True

    def test_trailing_slash_is_stripped(self):
        base, _masked, _allow = activate_qb_base_url("https://qb.internal:8080/", False)
        assert base == "https://qb.internal:8080"

    def test_preexisting_insecure_flag_preserved_for_https(self):
        # An operator who already allowed insecure http keeps that flag on https too.
        _base, _masked, allow = activate_qb_base_url("https://qb.internal:8080", True)
        assert allow is True
