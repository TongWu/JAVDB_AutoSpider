from __future__ import annotations

from collections.abc import Callable, Iterable, MutableMapping
from typing import Any

from javdb.parsing.models import MovieIndexEntry

FilterSpy = Callable[
    [list[MovieIndexEntry], Iterable[str] | None, MutableMapping[str, int] | None],
    list[MovieIndexEntry],
]


def _entry(code: str, family: str) -> MovieIndexEntry:
    return MovieIndexEntry(href=f"/v/{code}", video_code=code, video_code_family=family)


def spy_filter(
    calls: list[tuple[list[str], set[str]]],
    original_filter: FilterSpy,
) -> FilterSpy:
    def _spy_filter(
        movies: list[MovieIndexEntry],
        blacklist: Iterable[str] | None,
        counts: MutableMapping[str, int] | None = None,
    ) -> list[MovieIndexEntry]:
        calls.append(([movie.video_code for movie in movies], set(blacklist or [])))
        return original_filter(movies, blacklist, counts)

    return _spy_filter


def spy_select(
    calls: list[tuple[int, list[str]] | tuple[int, int, list[str]]],
    *,
    include_page_num: bool = False,
) -> Callable[..., list[dict[str, Any]]]:
    def _spy_select(
        page_result: Any,
        *,
        page_num: int,
        phase: int,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        payload = [movie.video_code for movie in page_result.movies]
        if include_page_num:
            calls.append((page_num, phase, payload))
        else:
            calls.append((phase, payload))
        return [movie.to_legacy_dict() for movie in page_result.movies]

    return _spy_select
