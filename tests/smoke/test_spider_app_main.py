"""Unit tests for spider main backend selection helpers."""

from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def test_create_detail_backend_selects_parallel(monkeypatch):
    import scripts.spider.app.main as spider_main
    import packages.python.javdb_spider.app.run_service as run_service

    sentinel = object()
    calls = []

    # ``create_detail_backend`` lives in run_service (main.py re-exports it
    # after W3.5), so the builders it calls must be patched on the
    # canonical module — patching spider_main would have no effect.
    monkeypatch.setattr(
        run_service,
        'build_parallel_detail_backend',
        lambda **kwargs: calls.append(kwargs) or sentinel,
    )
    monkeypatch.setattr(
        run_service,
        'build_sequential_detail_backend',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('sequential builder should not be called')
        ),
    )

    backend = spider_main.create_detail_backend(
        use_parallel=True,
        use_cookie=True,
        is_adhoc_mode=False,
        session=object(),
        use_proxy=True,
        use_cf_bypass=False,
    )

    assert backend is sentinel
    assert calls == [
        {
            'use_cookie': True,
            'use_proxy': True,
            'use_cf_bypass': False,
        }
    ]


def test_create_detail_backend_selects_sequential(monkeypatch):
    import scripts.spider.app.main as spider_main
    import packages.python.javdb_spider.app.run_service as run_service

    sentinel = object()
    session = object()
    calls = []

    # See parallel-case test above for why builders are patched on
    # run_service rather than spider_main (W3.5 re-export).
    monkeypatch.setattr(
        run_service,
        'build_parallel_detail_backend',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('parallel builder should not be called')
        ),
    )
    monkeypatch.setattr(
        run_service,
        'build_sequential_detail_backend',
        lambda *args, **kwargs: calls.append((args, kwargs)) or sentinel,
    )

    backend = spider_main.create_detail_backend(
        use_parallel=False,
        use_cookie=False,
        is_adhoc_mode=True,
        session=session,
        use_proxy=False,
        use_cf_bypass=True,
    )

    assert backend is sentinel
    assert calls == [
        (
            (session,),
            {
                'use_cookie': False,
                'is_adhoc_mode': True,
                'use_proxy': False,
                'use_cf_bypass': True,
            },
        )
    ]
