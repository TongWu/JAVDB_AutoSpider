import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.qb_config import (  # noqa: E402
    build_qb_base_url,
    qb_verify_tls,
)


def test_build_qb_base_url_defaults_to_https_when_requested():
    assert (
        build_qb_base_url("qb.internal", "8080", scheme="https")
        == "https://qb.internal:8080"
    )


def test_build_qb_base_url_blocks_http_without_opt_in():
    with pytest.raises(ValueError, match="QB_SCHEME=http requires QB_ALLOW_INSECURE_HTTP=true"):
        build_qb_base_url(
            "qb.internal",
            "8080",
            scheme="http",
            allow_insecure_http=False,
        )


def test_build_qb_base_url_allows_http_with_explicit_opt_in():
    assert (
        build_qb_base_url(
            "qb.internal",
            "8080",
            scheme="http",
            allow_insecure_http=True,
        )
        == "http://qb.internal:8080"
    )


def test_qb_verify_tls_parses_false_string():
    assert qb_verify_tls("false") is False
