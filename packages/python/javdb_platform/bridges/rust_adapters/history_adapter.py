"""History adapter entrypoints for optional Rust-backed behavior."""

from __future__ import annotations

from packages.python.javdb_platform.history_manager import (
    RUST_HISTORY_AVAILABLE,
    load_parsed_movies_history,
    save_parsed_movie_to_history,
    should_process_movie,
    has_complete_subtitles,
)

__all__ = [
    "RUST_HISTORY_AVAILABLE",
    "load_parsed_movies_history",
    "save_parsed_movie_to_history",
    "should_process_movie",
    "has_complete_subtitles",
]

