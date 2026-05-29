from __future__ import annotations

from apps.cli.pikpak.bridge import options_from_args, parse_args
from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.result import PikPakBridgeResult


def test_pikpak_options_defaults():
    options = PikPakBridgeOptions()

    assert options.days == 3
    assert options.dry_run is False
    assert options.batch_mode is True
    assert options.proxy_override is None
    assert options.from_pipeline is False
    assert options.session_id is None
    assert options.root_folder is None


def test_pikpak_cli_individual_turns_off_batch_mode():
    options = options_from_args(parse_args(["--individual", "--days", "5"]))

    assert options.days == 5
    assert options.batch_mode is False


def test_pikpak_result_default_exit_code_matches_current_cli_behavior():
    result = PikPakBridgeResult(total_torrents=4, filtered_old=4, failed_count=4)

    assert result.exit_code == 0
