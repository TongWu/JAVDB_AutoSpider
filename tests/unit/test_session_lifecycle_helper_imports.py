from __future__ import annotations

from apps.cli.db import _session_helpers as cli_helpers
from javdb.storage.rollback import session_helpers as rollback_helpers
from javdb.storage.sessions import lifecycle_helpers as canonical


def test_legacy_wrappers_reexport_canonical_helpers():
    for name in [
        "normalize_run_started_at",
        "fanout_movie_claim",
        "append_jsonl_record",
        "write_github_output",
        "attach_run_identity",
        "SessionPreState",
        "_FanoutConfig",
        "_FANOUT_CONFIGS",
        "db_get_session_status",
        "db_find_sessions_by_run",
        "db_find_in_progress_sessions",
        "create_movie_claim_client_from_env",
        "current_shard_date",
    ]:
        assert getattr(cli_helpers, name) is getattr(canonical, name)
        assert getattr(rollback_helpers, name) is getattr(canonical, name)
