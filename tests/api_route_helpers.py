"""FastAPI route inspection helpers for tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def iter_effective_routes(app: Any) -> Iterator[Any]:
    """Yield concrete routes across FastAPI eager and lazy router internals."""

    def expand(route: Any) -> Iterator[Any]:
        effective_candidates = getattr(route, "effective_candidates", None)
        if callable(effective_candidates):
            for candidate in effective_candidates():
                yield from expand(candidate)
            return
        yield route

    for route in app.routes:
        yield from expand(route)


def route_paths(app: Any) -> set[str]:
    return {
        path
        for route in iter_effective_routes(app)
        if isinstance(path := getattr(route, "path", None), str)
    }
