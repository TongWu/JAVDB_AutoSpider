"""Characterization tests for the per-proxy direct->CF->mark cascade.

These pin the observable behaviour of the single-proxy fallback sequence that
both `fetch_index_page_with_fallback` and `fetch_detail_page_with_fallback`
run (the `try_proxy_direct_then_cf` closure) BEFORE it is hoisted into a shared
`_attempt_via_proxy` helper. They exist so the extraction can be proven
behaviour-preserving: same returns, same `_mark_proxy_cf_bypass` calls, and the
index-saves / detail-does-not-save ban-HTML divergence must survive verbatim.

Each test drives the full twin into Phase 2 (single-proxy pool, login disabled,
non-adhoc) so the cascade runs exactly once, and controls success/failure per
fetch attempt through the validator/parser seam rather than HTML content.
"""

from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import javdb.spider.fetch.fallback as fallback_mod
import javdb.spider.runtime.state as state_mod
from javdb.infra.request import ProxyBannedError

_STUB = "<html>stub</html>"
_INDEX_FAIL = (None, False, False)            # (html, has_movie_list, is_valid_empty)
_INDEX_OK = (_STUB, True, False)
_DETAIL_FAIL = ([], "", "", "", "", False, None)
_DETAIL_OK = (["magnet:?xt=1"], "Actor", "", "", "", True, object())


def _pool():
    pool = MagicMock()
    pool.get_current_proxy_name.return_value = "P1"
    pool.get_proxy_count.return_value = 1
    pool.mark_failure_and_switch.return_value = True
    return pool


def _common_patches(stack, *, needs_cf, get_page, mark, save):
    """Apply the shared runtime=None fake wiring; return the configured pool."""
    pool = _pool()
    stack.enter_context(patch.object(state_mod, "global_proxy_pool", pool))
    stack.enter_context(patch.object(fallback_mod, "PROXY_MODE", "pool"))
    stack.enter_context(patch.object(fallback_mod, "_sleep_between_fetches"))
    stack.enter_context(patch.object(fallback_mod, "is_login_page", return_value=False))
    stack.enter_context(patch.object(fallback_mod, "can_attempt_login", return_value=False))
    stack.enter_context(patch.object(fallback_mod, "_proxy_needs_cf_bypass", return_value=needs_cf))
    stack.enter_context(patch.object(fallback_mod, "_mark_proxy_cf_bypass", mark))
    stack.enter_context(patch.object(fallback_mod, "_save_proxy_ban_html", save))
    if isinstance(get_page, list):
        stack.enter_context(patch.object(state_mod, "get_page", side_effect=get_page))
    else:
        stack.enter_context(patch.object(state_mod, "get_page", return_value=get_page))
    return pool


def _run_index(validate_seq, **kw):
    mark, save = MagicMock(), MagicMock()
    with ExitStack() as stack:
        _common_patches(stack, mark=mark, save=save, **kw)
        stack.enter_context(patch.object(fallback_mod, "validate_index_html", side_effect=validate_seq))
        result = fallback_mod.fetch_index_page_with_fallback(
            page_url="http://javdb.com/1", session=MagicMock(),
            use_cookie=True, use_proxy=True, use_cf_bypass=False, page_num=1,
        )
    return result, mark, save


def _run_detail(parse_seq, **kw):
    mark, save = MagicMock(), MagicMock()
    with ExitStack() as stack:
        _common_patches(stack, mark=mark, save=save, **kw)
        stack.enter_context(patch.object(fallback_mod, "_parse_detail_to_tuple", side_effect=parse_seq))
        result = fallback_mod.fetch_detail_page_with_fallback(
            detail_url="http://javdb.com/v/abc", session=MagicMock(),
            use_cookie=True, use_proxy=True, use_cf_bypass=False, entry_index="1/10",
        )
    return result, mark, save


# ── Index twin ──────────────────────────────────────────────────────────────

def test_index_phase2_direct_fail_then_cf_success_marks_bypass():
    # Phase0 direct+CF fail, Phase2 direct fails, Phase2 CF succeeds.
    result, mark, save = _run_index(
        validate_seq=[_INDEX_FAIL, _INDEX_FAIL, _INDEX_FAIL, _INDEX_OK],
        needs_cf=False, get_page=_STUB,
    )
    assert result[1] is True            # has_movie_list
    assert result[4] is True            # effective_use_cf_bypass (CF won)
    mark.assert_called_once_with("P1", runtime=None)


def test_index_phase2_marked_proxy_goes_straight_to_cf_without_remark():
    # _proxy_needs_cf_bypass True -> marked CF path; success must NOT re-mark.
    result, mark, save = _run_index(
        validate_seq=[_INDEX_FAIL, _INDEX_OK],
        needs_cf=True, get_page=_STUB,
    )
    assert result[1] is True
    assert result[4] is True
    mark.assert_not_called()


def test_index_phase2_proxy_banned_saves_ban_html():
    # Phase0 direct+CF fail (stub), Phase2 direct raises ProxyBanned.
    result, mark, save = _run_index(
        validate_seq=[_INDEX_FAIL, _INDEX_FAIL],
        needs_cf=False,
        get_page=[_STUB, _STUB, ProxyBannedError("P1", "banned", "<ban/>")],
    )
    assert result[2] is True            # proxy_was_banned
    assert save.called                  # index persists ban HTML


# ── Detail twin ─────────────────────────────────────────────────────────────

def test_detail_phase2_direct_fail_then_cf_success_marks_bypass():
    result, mark, save = _run_detail(
        parse_seq=[_DETAIL_FAIL, _DETAIL_FAIL, _DETAIL_FAIL, _DETAIL_OK],
        needs_cf=False, get_page=_STUB,
    )
    assert result[5] is True            # parse_success
    assert result[8] is True            # effective_use_cf_bypass
    mark.assert_called_once_with("P1", runtime=None)


def test_detail_phase2_marked_proxy_goes_straight_to_cf_without_remark():
    result, mark, save = _run_detail(
        parse_seq=[_DETAIL_FAIL, _DETAIL_OK],
        needs_cf=True, get_page=_STUB,
    )
    assert result[5] is True
    assert result[8] is True
    mark.assert_not_called()


def test_detail_phase2_proxy_banned_does_not_save_ban_html():
    # Divergence from index: detail's ban handler only logs, never saves.
    result, mark, save = _run_detail(
        parse_seq=[_DETAIL_FAIL, _DETAIL_FAIL],
        needs_cf=False,
        get_page=[_STUB, _STUB, ProxyBannedError("P1", "banned", "<ban/>")],
    )
    assert result[5] is False           # parse_success (best-effort empty)
    assert not save.called              # detail never persists ban HTML
