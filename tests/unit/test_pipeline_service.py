from __future__ import annotations

from types import SimpleNamespace

import pytest

from packages.python.javdb_platform import pipeline_service


def _make_args(**overrides):
    defaults = {
        'url': None,
        'start_page': None,
        'end_page': None,
        'all': False,
        'ignore_history': False,
        'phase': None,
        'output_file': 'Javdb_Test.csv',
        'dry_run': False,
        'ignore_release_date': False,
        'use_proxy': False,
        'no_proxy': False,
        'always_bypass_time': None,
        'pikpak_individual': False,
        'enable_dedup': False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_pipeline_main_uses_auto_proxy_by_default(monkeypatch):
    recorded_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def fake_run_command(command, args=None):
        recorded_calls.append((tuple(command), tuple(args or [])))
        if command[-1] == 'apps.cli.spider':
            return "SPIDER_OUTPUT_CSV=reports/DailyReport/2026/03/Javdb_Test.csv\nSPIDER_SESSION_ID=273\n"
        return ""

    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args())
    monkeypatch.setattr(pipeline_service, 'check_rust_core_status', lambda: None)
    monkeypatch.setattr(pipeline_service, 'run_command', fake_run_command)

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.spider')
    uploader_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.qb_uploader')
    pikpak_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.pikpak_bridge')

    assert '--use-proxy' not in spider_call[1]
    assert '--no-proxy' not in spider_call[1]
    assert '--use-proxy' not in uploader_call[1]
    assert '--no-proxy' not in uploader_call[1]
    assert '--use-proxy' not in pikpak_call[1]
    assert '--no-proxy' not in pikpak_call[1]
    assert '--session-id' in uploader_call[1]


def test_pipeline_main_force_enables_proxy_for_all_steps(monkeypatch):
    recorded_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def fake_run_command(command, args=None):
        recorded_calls.append((tuple(command), tuple(args or [])))
        if command[-1] == 'apps.cli.spider':
            return "SPIDER_OUTPUT_CSV=reports/DailyReport/2026/03/Javdb_Test.csv\nSPIDER_SESSION_ID=273\n"
        return ""

    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(use_proxy=True))
    monkeypatch.setattr(pipeline_service, 'check_rust_core_status', lambda: None)
    monkeypatch.setattr(pipeline_service, 'run_command', fake_run_command)

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.spider')
    uploader_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.qb_uploader')
    pikpak_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.pikpak_bridge')

    assert '--use-proxy' in spider_call[1]
    assert '--use-proxy' in uploader_call[1]
    assert '--use-proxy' in pikpak_call[1]


def test_pipeline_main_force_disables_proxy_for_all_steps(monkeypatch):
    recorded_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def fake_run_command(command, args=None):
        recorded_calls.append((tuple(command), tuple(args or [])))
        if command[-1] == 'apps.cli.spider':
            return "SPIDER_OUTPUT_CSV=reports/DailyReport/2026/03/Javdb_Test.csv\nSPIDER_SESSION_ID=273\n"
        return ""

    monkeypatch.setattr(pipeline_service, 'parse_arguments', lambda: _make_args(no_proxy=True))
    monkeypatch.setattr(pipeline_service, 'check_rust_core_status', lambda: None)
    monkeypatch.setattr(pipeline_service, 'run_command', fake_run_command)

    with pytest.raises(SystemExit) as exc:
        pipeline_service.main()

    assert exc.value.code == 0

    spider_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.spider')
    uploader_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.qb_uploader')
    pikpak_call = next(call for call in recorded_calls if call[0][-1] == 'apps.cli.pikpak_bridge')

    assert '--no-proxy' in spider_call[1]
    assert '--no-proxy' in uploader_call[1]
    assert '--no-proxy' in pikpak_call[1]
