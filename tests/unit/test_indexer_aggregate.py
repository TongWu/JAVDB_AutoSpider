"""Tests for ADR-054 WS3 indexer magnet aggregation."""

from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult


def _result(source, magnets):
    return IndexerResult(source=source, ok=True, magnets=magnets)


def _magnet(source, uri, name="ABC-001", info_hash=None, tags=None):
    return IndexerMagnet(
        magnet_uri=uri,
        name=name,
        source=source,
        info_hash=info_hash,
        size="1.2 GB",
        tags=list(tags or []),
        file_count=1,
    )


def test_same_infohash_across_sources_merges_and_lowercases_hash(monkeypatch):
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [
                    _magnet(
                        "JAVBUS",
                        "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12",
                        info_hash="ABCDEF1234567890ABCDEF1234567890ABCDEF12",
                    )
                ],
            ),
            _result(
                "Sukebei",
                [
                    _magnet(
                        "Sukebei",
                        "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12",
                        info_hash=" abcdef1234567890abcdef1234567890abcdef12 ",
                    )
                ],
            ),
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert len(rows) == 1
    assert rows[0]["info_hash"] == "abcdef1234567890abcdef1234567890abcdef12"
    assert rows[0]["sources"] == ["JAVBUS", "Sukebei"]


def test_distinct_infohashes_survive(monkeypatch):
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [
                    _magnet(
                        "JAVBUS",
                        "magnet:?xt=urn:btih:1111111111111111111111111111111111111111",
                    )
                ],
            ),
            _result(
                "Sukebei",
                [
                    _magnet(
                        "Sukebei",
                        "magnet:?xt=urn:btih:2222222222222222222222222222222222222222",
                    )
                ],
            ),
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert len(rows) == 2
    assert {row["info_hash"] for row in rows} == {
        "1111111111111111111111111111111111111111",
        "2222222222222222222222222222222222222222",
    }


def test_live_score_marks_probe_unavailable(monkeypatch):
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [
                    _magnet(
                        "JAVBUS",
                        "magnet:?xt=urn:btih:1111111111111111111111111111111111111111",
                        tags=["subtitle"],
                    )
                ],
            )
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert "probe_unavailable" in rows[0]["quality_reasons"]


def test_hashless_magnets_across_sources_do_not_collapse(monkeypatch):
    # issue #224: a magnet with no resolvable info-hash can't be deduped, so each
    # must survive as its own row. The old code keyed every hashless magnet to a
    # single video_code group, silently dropping N-1 of N results.
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [_magnet("JAVBUS", "magnet:?xt=urn:btmh:bad-v2-only", name="from-javbus")],
            ),
            _result(
                "Sukebei",
                [_magnet("Sukebei", "not a magnet", name="from-sukebei")],
            ),
        ],
    )

    # Fullwidth ａｂｃ + surrounding whitespace: deliberately messy operator input
    # the aggregator must tolerate; the ambiguous-unicode lint flag is intentional.
    rows = agg.aggregate_magnets(" ａｂｃ-001 ")  # noqa: RUF001

    assert len(rows) == 2
    assert all(row["info_hash"] is None for row in rows)
    assert {row["sources"][0] for row in rows} == {"JAVBUS", "Sukebei"}
    assert {row["name"] for row in rows} == {"from-javbus", "from-sukebei"}


def test_multiple_hashless_magnets_same_source_each_survive(monkeypatch):
    # issue #224: two hashless magnets from the SAME source also must not collapse.
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "Sukebei",
                [
                    _magnet("Sukebei", "magnet:?dn=one", name="one"),
                    _magnet("Sukebei", "magnet:?dn=two", name="two"),
                ],
            ),
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert len(rows) == 2
    assert {row["name"] for row in rows} == {"one", "two"}


def test_duplicate_infohash_keeps_higher_quality_score_and_reasons(monkeypatch):
    from javdb.integrations.indexer import aggregate as agg

    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [
                    _magnet(
                        "JAVBUS",
                        "magnet:?xt=urn:btih:1111111111111111111111111111111111111111",
                        name="plain",
                        tags=["subtitle"],
                    )
                ],
            ),
            _result(
                "Sukebei",
                [
                    _magnet(
                        "Sukebei",
                        "magnet:?xt=urn:btih:1111111111111111111111111111111111111111",
                        name="ABC-001 中文字幕",
                        tags=["subtitle"],
                    )
                ],
            ),
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert len(rows) == 1
    assert rows[0]["name"] == "ABC-001 中文字幕"
    assert rows[0]["quality_score"] > 0
    assert "subtitle_name_hint" in rows[0]["quality_reasons"]
    assert rows[0]["sources"] == ["JAVBUS", "Sukebei"]


def test_base32_btih_uri_is_normalized_when_info_hash_missing(monkeypatch):
    from javdb.integrations.indexer import aggregate as agg

    expected = "abcdef1234567890abcdef1234567890abcdef12"
    base32_btih = "VPG66ERUKZ4JBK6N54JDIVTYSCV433YS"
    monkeypatch.setattr(
        agg,
        "_collect",
        lambda video_code: [
            _result(
                "JAVBUS",
                [
                    _magnet(
                        "JAVBUS",
                        f"magnet:?xt=urn:btih:{base32_btih}",
                        info_hash=None,
                    )
                ],
            ),
            _result(
                "Sukebei",
                [
                    _magnet(
                        "Sukebei",
                        f"magnet:?xt=urn:btih:{expected}",
                        info_hash="not-a-hash",
                    )
                ],
            ),
        ],
    )

    rows = agg.aggregate_magnets("ABC-001")

    assert len(rows) == 1
    assert rows[0]["info_hash"] == expected
    assert rows[0]["sources"] == ["JAVBUS", "Sukebei"]
