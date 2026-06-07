"""A clean daily run: 1 index page -> 2 movies -> 2 detail pages with magnets.

Fixtures mirror the proven selectors in ``tests/conftest.py``'s
``sample_index_html`` / ``sample_detail_html`` (``div.movie-list`` ->
``div.item`` -> ``a.box``; ``div#magnets-content`` -> ``div.item`` ->
``a[href^=magnet]``). Each detail page carries one 40-hex magnet so FakeQB
computes a stable hash. Index URL is the value ``get_page_url(1)`` computes."""

from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario

# Cassette keys — exactly what the spider requests.
INDEX_URL = "https://javdb.com?page=1"
DETAIL_URL_1 = "https://javdb.com/v/AAA111"
DETAIL_URL_2 = "https://javdb.com/v/BBB222"

MAGNET_1 = "magnet:?xt=urn:btih:" + "a" * 40
MAGNET_2 = "magnet:?xt=urn:btih:" + "b" * 40

INDEX_HTML = """
<html>
<head><title>JavDB</title></head>
<body>
    <div class="movie-list h cols-4 vcols-8">
        <div class="item">
            <a class="box" href="/v/AAA111">
                <div class="video-title"><strong>ABC-001</strong> Title One</div>
                <div class="tags has-addons">
                    <span class="tag is-warning">含中字磁鏈</span>
                    <span class="tag">今日新種</span>
                </div>
                <div class="score">
                    <span class="value">4.47分, 由595人評價</span>
                </div>
            </a>
        </div>
        <div class="item">
            <a class="box" href="/v/BBB222">
                <div class="video-title"><strong>ABC-002</strong> Title Two</div>
                <div class="tags has-addons">
                    <span class="tag is-warning">含中字磁鏈</span>
                    <span class="tag">今日新種</span>
                </div>
                <div class="score">
                    <span class="value">4.52分, 由120人評價</span>
                </div>
            </a>
        </div>
    </div>
</body>
</html>
"""


def _detail(code: str, magnet: str) -> str:
    return f"""
<html>
<head><title>{code} Detail</title></head>
<body>
    <div class="video-meta-panel">
        <div class="panel-block">
            <strong>演員:</strong>
            <span class="value">
                <a href="/actors/xyz">Sample Actor</a><strong class="symbol female">♀</strong>&nbsp;
            </span>
        </div>
    </div>
    <div id="magnets-content">
        <div class="item columns is-desktop">
            <div class="magnet-name">
                <a href="{magnet}">
                    <span class="name">{code}-subtitle.torrent</span>
                    <span class="meta">4.94GB, 1個文件</span>
                    <div class="tags">
                        <span class="tag">字幕</span>
                    </div>
                </a>
            </div>
            <span class="time">2024-01-15</span>
        </div>
    </div>
</body>
</html>
"""


DETAIL_HTML_1 = _detail("ABC-001", MAGNET_1)
DETAIL_HTML_2 = _detail("ABC-002", MAGNET_2)


def golden_daily() -> PipelineScenario:
    return PipelineScenario(
        pages={
            INDEX_URL: INDEX_HTML,
            DETAIL_URL_1: DETAIL_HTML_1,
            DETAIL_URL_2: DETAIL_HTML_2,
        },
        qb=FakeQBConfig(),
    )
