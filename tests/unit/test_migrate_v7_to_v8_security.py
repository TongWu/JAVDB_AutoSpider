import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_migrations.tools import migrate_v7_to_v8  # noqa: E402


def test_actor_log_fields_only_return_presence_flags_and_count():
    result = migrate_v7_to_v8._actor_log_fields(
        "Actor Name",
        "https://javdb.com/actors/abc",
        '[{"name":"A"},{"name":"B"}]',
    )

    assert result == (True, True, 2)
