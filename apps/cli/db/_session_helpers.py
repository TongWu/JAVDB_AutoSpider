"""Backward-compatibility shim — all helpers now live in javdb.storage.rollback.session_helpers."""
from javdb.storage.rollback.session_helpers import *  # noqa: F401,F403
from javdb.storage.rollback.session_helpers import (
    SessionPreState,
    _FanoutConfig,
    _FANOUT_CONFIGS,
    normalize_run_started_at,
    find_run_sessions,
    find_window_sessions,
    read_session_pre_state,
    fanout_movie_claim,
    append_jsonl_record,
    write_github_output,
    attach_run_identity,
)  # noqa: F401  -- private/underscore symbols not picked up by `*`
