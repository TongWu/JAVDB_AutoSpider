"""Predefined tag vocabulary for MovieRatings (ADR-022)."""

VALID_TAGS: frozenset[str] = frozenset({
    # Quality / Technical
    "quality_high",
    "quality_low",
    "resolution_bad",
    "encoding_bad",
    # Content preference
    "plot_good",
    "actress_standout",
    "not_my_type",
    "category_miss",
    # Collection / Decision
    "would_rewatch",
    "keep_long_term",
    "delete_candidate",
    "upgrade_wanted",
})

TAG_GROUPS: dict[str, list[str]] = {
    "quality":    ["quality_high", "quality_low", "resolution_bad", "encoding_bad"],
    "content":    ["plot_good", "actress_standout", "not_my_type", "category_miss"],
    "collection": ["would_rewatch", "keep_long_term", "delete_candidate", "upgrade_wanted"],
}
