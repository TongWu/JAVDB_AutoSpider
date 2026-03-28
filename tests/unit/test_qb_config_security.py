import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.qb_config import (  # noqa: E402
    build_qb_base_url,
    qb_base_url_candidates,
    qb_verify_tls,
)


def test_build_qb_base_url_supports_legacy_host_port_input():
    assert (
        build_qb_base_url("qb.internal", "8080", scheme="https")
        == "https://qb.internal:8080"
    )


def test_build_qb_base_url_defaults_to_https_when_scheme_is_missing():
    assert build_qb_base_url("qb.internal:8080") == "https://qb.internal:8080"


def test_qb_base_url_candidates_includes_http_fallback_by_default():
    assert qb_base_url_candidates("qb.internal:8080") == [
        "https://qb.internal:8080",
        "http://qb.internal:8080",
    ]


def test_qb_base_url_candidates_http_fallback_when_insecure_allowed():
    assert qb_base_url_candidates(
        "qb.internal:8080", allow_insecure_http=True,
    ) == [
        "https://qb.internal:8080",
        "http://qb.internal:8080",
    ]


def test_build_qb_base_url_rejects_http_without_flag():
    with pytest.raises(ValueError, match="insecure"):
        build_qb_base_url("http://qb.internal:8080")


def test_build_qb_base_url_accepts_explicit_http_url_with_flag():
    assert build_qb_base_url(
        "http://qb.internal:8080", allow_insecure_http=True,
    ) == "http://qb.internal:8080"


def test_build_qb_base_url_rejects_invalid_scheme():
    with pytest.raises(ValueError, match="QB_URL must start with http:// or https://"):
        build_qb_base_url("ftp://qb.internal:8080")


def test_qb_verify_tls_parses_false_string():
    assert qb_verify_tls("false") is False
