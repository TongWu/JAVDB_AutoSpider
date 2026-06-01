from __future__ import annotations

from javdb.parsing.models import MovieIndexEntry


def _entry(code, family):
    return MovieIndexEntry(href=f"/v/{code}", video_code=code, video_code_family=family)


def spy_filter(calls, original_filter):
    def _spy_filter(movies, blacklist, counts=None):
        calls.append(([movie.video_code for movie in movies], set(blacklist)))
        return original_filter(movies, blacklist, counts)

    return _spy_filter


def spy_select(calls, *, include_page_num: bool = False):
    def _spy_select(page_result, *, page_num, phase, **_kwargs):
        payload = [movie.video_code for movie in page_result.movies]
        if include_page_num:
            calls.append((page_num, phase, payload))
        else:
            calls.append((phase, payload))
        return [movie.to_legacy_dict() for movie in page_result.movies]

    return _spy_select
