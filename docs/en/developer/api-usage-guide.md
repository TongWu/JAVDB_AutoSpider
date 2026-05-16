# JAVDB AutoSpider API Usage Guide

This document explains how to use JAVDB AutoSpider's parsing capabilities through two interfaces: the **Python module API** and the **REST API**.

---

## Table of Contents

- [Installation](#installation)
- [1. Python Module API](#1-python-module-api)
  - [1.1 Parsing Index Pages](#11-parsing-index-pages)
  - [1.2 Parsing Movie Detail Pages](#12-parsing-movie-detail-pages)
  - [1.3 Parsing Category Pages](#13-parsing-category-pages)
  - [1.4 Parsing Ranking Pages](#14-parsing-ranking-pages)
  - [1.5 Parsing Tag Filter Pages](#15-parsing-tag-filter-pages)
  - [1.6 Auto-Detecting Page Type](#16-auto-detecting-page-type)
- [2. REST API](#2-rest-api)
  - [2.1 Starting the Server](#21-starting-the-server)
  - [2.2 Health Check](#22-health-check)
  - [2.3 Parsing Index Pages](#23-parsing-index-pages)
  - [2.4 Parsing Movie Detail Pages](#24-parsing-movie-detail-pages)
  - [2.5 Parsing Category Pages](#25-parsing-category-pages)
  - [2.6 Parsing Ranking Pages](#26-parsing-ranking-pages)
  - [2.7 Parsing Tag Filter Pages](#27-parsing-tag-filter-pages)
  - [2.8 Detecting Page Type](#28-detecting-page-type)
- [3. Data Model Reference](#3-data-model-reference)

---

## Installation

```bash
pip install -r requirements.txt
```

Core dependencies: `beautifulsoup4`, `lxml`, `javdb_rust_core` (optional). The REST API additionally requires: `fastapi`, `uvicorn`.

> **Rust first, Python fallback**: When `javdb_rust_core` (a Rust extension compiled via PyO3 + maturin) is available, the system automatically uses the Rust parser implementation for a 5--10x performance boost. When `javdb_rust_core` is unavailable, it falls back to the pure Python implementation using `beautifulsoup4` / `lxml`. This switch is completely transparent to callers -- no API call changes are needed.

---

## 1. Python Module API

All parsing functions accept an **HTML string** and return **structured dataclass objects**. The parsers perform no business-level filtering (no phase1/phase2 distinction, no subtitle/date tag filtering) and return all raw data present on the page.

### 1.1 Parsing Index Pages

Parses any page containing a movie list (works for the home page, category pages, ranking pages, etc.).

```python
from apps.api.parsers import parse_index_page

# Read HTML content
with open('page.html', 'r', encoding='utf-8') as f:
    html = f.read()

result = parse_index_page(html, page_num=1)

# Basic information
print(result.has_movie_list)  # True / False
print(result.page_title)      # Page title
print(len(result.movies))     # Number of movies

# Iterate over each movie
for movie in result.movies:
    print(f"Code: {movie.video_code}")
    print(f"Title: {movie.title}")
    print(f"Rating: {movie.rate}")
    print(f"Review count: {movie.comment_count}")
    print(f"Release date: {movie.release_date}")
    print(f"Tags: {movie.tags}")           # ['含中字磁鏈', '今日新種']
    print(f"Cover: {movie.cover_url}")
    print(f"Link: {movie.href}")
    print(f"Ranking: {movie.ranking}")     # Only populated on ranking pages
    print()

# Convert to dict (convenient for JSON serialization)
data = result.to_dict()
```

**Return type:** `IndexPageResult`

| Field | Type | Description |
|-------|------|-------------|
| `has_movie_list` | `bool` | Whether the page contains a movie list |
| `movies` | `List[MovieIndexEntry]` | All movie entries |
| `page_title` | `str` | Page `<title>` text |

**MovieIndexEntry fields:**

| Field | Type | Description |
|-------|------|-------------|
| `href` | `str` | Movie detail page link |
| `video_code` | `str` | Video code (e.g., `"STAR-123"`) |
| `title` | `str` | Movie title |
| `rate` | `str` | Rating (e.g., `"4.47"`) |
| `comment_count` | `str` | Number of reviews (e.g., `"595"`) |
| `release_date` | `str` | Release date (e.g., `"2026-02-11"`) |
| `tags` | `List[str]` | Page tags (e.g., `["含中字磁鏈", "今日新種"]`) |
| `cover_url` | `str` | Cover image URL |
| `page` | `int` | Page number |
| `ranking` | `Optional[int]` | Ranking (only populated on ranking pages) |

---

### 1.2 Parsing Movie Detail Pages

Extracts full metadata from a movie detail page.

```python
from apps.api.parsers import parse_detail_page

with open('detail.html', 'r', encoding='utf-8') as f:
    html = f.read()

detail = parse_detail_page(html)

# ---- Basic information ----
print(f"Title: {detail.title}")
print(f"Code: {detail.video_code}")
print(f"Code prefix link: {detail.code_prefix_link}")  # e.g., /video_codes/VDD
print(f"Duration: {detail.duration}")
print(f"Release date: {detail.release_date}")

# ---- Related entities (MovieLink: name + href) ----
if detail.maker:
    print(f"Maker: {detail.maker.name} ({detail.maker.href})")
if detail.publisher:
    print(f"Publisher: {detail.publisher.name}")
if detail.series:
    print(f"Series: {detail.series.name}")
for d in detail.directors:
    print(f"Director: {d.name}")
for a in detail.actors:
    # ActorCredit: name, href, gender ('female' / 'male' / '')
    print(f"Actor: {a.name} ({a.href}) [{a.gender}]")
for t in detail.tags:
    print(f"Genre tag: {t.name}")

# ---- Ratings ----
print(f"Rating: {detail.rate}")
print(f"Review count: {detail.comment_count}")
print(f"Short reviews: {detail.review_count}")
print(f"Want to watch: {detail.want_count} people")
print(f"Watched: {detail.watched_count} people")

# ---- Media resources ----
print(f"Poster: {detail.poster_url}")
print(f"Fanart: {detail.fanart_urls}")      # List of full-size image URLs
print(f"Trailer: {detail.trailer_url}")

# ---- Magnet links ----
for m in detail.magnets:
    print(f"Magnet: {m.name} | Size: {m.size} | Tags: {m.tags} | Date: {m.timestamp}")
    print(f"  Link: {m.href}")

# ---- Legacy interface compatibility / Lead and supporting actors ----
actor_name = detail.get_first_actor_name()        # First (lead) actor name
actor_gender = detail.get_first_actor_gender()    # Lead actor gender
supporting_json = detail.get_supporting_actors_json()  # Supporting actors JSON (for DB storage)
d = detail.to_dict()  # Includes lead_actor and supporting_actors convenience fields
magnets_list = detail.get_magnets_as_legacy()     # List[dict] format
```

**Return type:** `MovieDetail`

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Movie title |
| `video_code` | `str` | Video code |
| `code_prefix_link` | `str` | Code prefix page link (e.g., `/video_codes/VDD`) |
| `duration` | `str` | Duration |
| `release_date` | `str` | Release date |
| `publisher` | `Optional[MovieLink]` | Publisher |
| `maker` | `Optional[MovieLink]` | Maker |
| `series` | `Optional[MovieLink]` | Series |
| `directors` | `List[MovieLink]` | List of directors |
| `tags` | `List[MovieLink]` | List of genre tags |
| `rate` | `str` | Rating |
| `comment_count` | `str` | Number of reviews |
| `poster_url` | `str` | Poster URL |
| `fanart_urls` | `List[str]` | List of fanart URLs |
| `trailer_url` | `Optional[str]` | Trailer URL |
| `actors` | `List[ActorCredit]` | List of actors (order matches page; includes `gender`) |
| `lead_actor` | `Optional[dict]` | Lead actor `{name, href, gender}` in `to_dict()` output |
| `supporting_actors` | `List[dict]` | Remaining actors in `to_dict()` output |
| `magnets` | `List[MagnetInfo]` | List of magnet links |
| `review_count` | `int` | Number of short reviews |
| `want_count` | `int` | Number of "want to watch" |
| `watched_count` | `int` | Number of "watched" |
| `parse_success` | `bool` | Whether the magnet links section was found |

---

### 1.3 Parsing Category Pages

Parses category pages for makers, publishers, series, directors, code prefixes, actors, etc., extracting additional category information.

```python
from apps.api.parsers import parse_category_page

result = parse_category_page(html, page_num=1)

print(f"Category type: {result.category_type}")   # e.g., 'makers', 'directors'
print(f"Category name: {result.category_name}")   # e.g., 'PRESTIGE'
print(f"Movie count: {len(result.movies)}")

# The movies field is identical to IndexPageResult
for movie in result.movies:
    print(f"  {movie.video_code} - {movie.title}")
```

**Return type:** `CategoryPageResult` (extends `IndexPageResult`)

| Additional field | Type | Description |
|------------------|------|-------------|
| `category_type` | `str` | Category type (`makers`, `publishers`, `series`, `directors`, `video_codes`, `actors`) |
| `category_name` | `str` | Category display name |

---

### 1.4 Parsing Ranking Pages

Parses Top250, daily/weekly/monthly ranking pages, etc.

```python
from apps.api.parsers import parse_top_page

result = parse_top_page(html, page_num=1)

print(f"Ranking type: {result.top_type}")   # 'top250', 'top_movies', 'top_playback'
print(f"Period: {result.period}")           # '2025', 'daily', 'weekly', 'monthly'

for movie in result.movies:
    print(f"  #{movie.ranking} {movie.video_code} - Rating: {movie.rate}")
```

**Return type:** `TopPageResult` (extends `IndexPageResult`)

| Additional field | Type | Description |
|------------------|------|-------------|
| `top_type` | `str` | Ranking type |
| `period` | `Optional[str]` | Time period |

---

### 1.5 Parsing Tag Filter Pages

Parses the `/tags` page, extracting the complete tag filter panel (all categories, all tag options, ID-to-name mappings) along with the movie list.

```python
from apps.api.parsers import parse_tag_page

result = parse_tag_page(html, page_num=1)

# ---- Movie list (same as IndexPageResult) ----
print(f"Movie count: {len(result.movies)}")

# ---- Current filter state ----
print(f"Current selections: {result.current_selections}")
# Output: {'1': '23', '5': '24', '6': '29', '7': '28', '11': '2026'}

# ---- View all categories ----
for cat in result.categories:
    print(f"\nCategory c{cat.category_id}: {cat.name}")
    for opt in cat.options:
        status = " [selected]" if opt.selected else ""
        id_info = f" (ID: {opt.tag_id})" if opt.tag_id else " (ID unknown)"
        print(f"  - {opt.name}{id_info}{status}")

# ---- Look up by category ID ----
cat4 = result.get_category_by_id('4')        # Body Type (體型)
print(f"Body Type category has {len(cat4.options)} tags")

# ---- Look up by category name (the API uses the Chinese name as identifier) ----
cat = result.get_category_by_name('行爲')     # Behavior
print(f"Behavior category ID: c{cat.category_id}")

# ---- Get ID-to-name mapping (names are returned in Chinese as the API stores them) ----
id_map = cat4.get_id_to_name_map()
print(id_map['15'])    # → '熟女'  (Mature Woman)
print(id_map['17'])    # → '巨乳'  (Big Breasts)

# ---- Get name-to-ID mapping (reverse lookup; key is the Chinese tag name) ----
name_map = cat4.get_name_to_id_map()
print(name_map['熟女'])  # → '15'

# ---- Get global mapping across all categories ----
full_map = result.get_full_id_to_name_map()
print(full_map[('4', '15')])   # → '熟女'     (Body Type category, ID 15)
print(full_map[('1', '23')])   # → '淫亂真實'  (Theme category, ID 23)
print(full_map[('7', '28')])   # → '單體作品'  (Type category, ID 28)

# ---- View selected tags in a category ----
cat7 = result.get_category_by_id('7')
for sel in cat7.get_selected():
    print(f"Selected: {sel.name} (ID: {sel.tag_id})")
```

**Return type:** `TagPageResult` (extends `IndexPageResult`)

| Additional field | Type | Description |
|------------------|------|-------------|
| `categories` | `List[TagCategory]` | All filter categories |
| `current_selections` | `dict` | Current selection state `{category_id: "tag_ids"}` |

**TagCategory fields:**

| Field | Type | Description |
|-------|------|-------------|
| `category_id` | `str` | Category ID (corresponds to URL parameter `c{N}`) |
| `name` | `str` | Category name in Chinese as returned by the API (e.g., "主題" / Theme, "體型" / Body Type) |
| `options` | `List[TagOption]` | All tag options under this category |

**TagCategory convenience methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `get_id_to_name_map()` | `dict` | `{tag_id: name}` mapping |
| `get_name_to_id_map()` | `dict` | `{name: tag_id}` reverse mapping |
| `get_selected()` | `List[TagOption]` | Currently selected tags |

**Tag Category ID Reference Table:**

The category names and tag values below are the **actual identifiers used by the JavDB API** (in Traditional Chinese). The English in parentheses is an explanatory translation only — when querying the API or matching against parser output, use the Chinese strings verbatim.

| URL Parameter | Category Name (CN / EN) | Example Tags (value with ID) |
|---------------|-------------------------|------------------------------|
| `c10` | 基本 (Basic) | 可播放 / Playable (6), 含磁鏈 / With Magnet (1), 含字幕 / With Subtitles (2) |
| `c11` | 年份 (Year) | 2026, 2025, 2024... |
| `c1` | 主題 (Theme) | 淫亂真實 / Promiscuous Realistic (23), 出軌 / Infidelity (51), 強姦 / Forced (52) |
| `c2` | 角色 (Role) | 高中女生 / High School Girl (1), 美少女 / Beautiful Girl (5), 已婚婦女 / Married Woman |
| `c3` | 服裝 (Costume) | 眼鏡 / Glasses (3), 角色扮演 / Cosplay (43), 制服 / Uniform |
| `c4` | 體型 (Body Type) | 熟女 / Mature Woman (15), 巨乳 / Big Breasts (17), 蘿莉塔 / Lolita |
| `c5` | 行爲 (Behavior) | 乳交 / Paizuri (14), 中出 / Creampie (18), 多P / Multiple Partners (24) |
| `c6` | 玩法 (Play Style) | 捆綁 / Bondage (29), 凌辱 / Humiliation, SM |
| `c7` | 類別 (Type) | 單體作品 / Solo Work (28), VR (212), 4K (347) |
| `c9` | 時長 (Duration) | lt-45, 45-90, 90-120, gt-120 |

> **Note:** The numbers in parentheses after a tag are tag IDs. When only a few categories are selected on the page, most tag IDs can be extracted from the HTML. When multiple categories are selected, some tags may have an empty `tag_id`. It is recommended to extract the complete mapping from a page with fewer selections (e.g., only one category selected).

---

### 1.6 Auto-Detecting Page Type

Not sure what type of page the HTML is? Let the parser auto-detect it.

```python
from apps.api.parsers import detect_page_type

page_type = detect_page_type(html)
# Returns: 'index', 'detail', 'top250', 'top_movies', 'makers',
#          'publishers', 'series', 'directors', 'video_codes',
#          'actors', 'tags', or 'unknown'
```

---

## 2. REST API

The REST API is a thin wrapper around the Python module API, built with the FastAPI framework. All parsing endpoints accept an HTML string and return JSON.

### 2.1 Starting the Server

```bash
# Development mode (auto-reload)
uvicorn apps.api.server:app --reload --port 8100

# Production mode
uvicorn apps.api.server:app --host 0.0.0.0 --port 8100 --workers 4
```

After starting, visit `http://localhost:8100/docs` to view the auto-generated Swagger documentation.

### 2.2 Health Check

```bash
curl http://localhost:8100/api/health
```

```json
{"status": "ok"}
```

### 2.3 Parsing Index Pages

```bash
curl -X POST http://localhost:8100/api/parse/index \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**Response example:**

```json
{
  "has_movie_list": true,
  "page_title": "JavDB",
  "movies": [
    {
      "href": "/v/ABC-123",
      "video_code": "ABC-123",
      "title": "Movie title...",
      "rate": "4.47",
      "comment_count": "595",
      "release_date": "2026-02-11",
      "tags": ["含中字磁鏈", "今日新種"],
      "cover_url": "https://..../cover.jpg",
      "page": 1,
      "ranking": null
    }
  ]
}
```

### 2.4 Parsing Movie Detail Pages

```bash
curl -X POST http://localhost:8100/api/parse/detail \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>"}'
```

**Response example:**

```json
{
  "title": "脅迫スイートルーム ...",
  "video_code": "VDD-201",
  "code_prefix_link": "/video_codes/VDD",
  "duration": "130分鍾",
  "release_date": "2026-02-06",
  "maker": {"name": "ドリームチケット", "href": "/makers/wm?f=download"},
  "publisher": null,
  "series": {"name": "脅迫スイートルーム", "href": "/series/KdqA"},
  "directors": [{"name": "沢庵", "href": "/directors/pz9"}],
  "tags": [
    {"name": "美乳", "href": "/tags?c4=..."},
    {"name": "女教師", "href": "/tags?c2=..."}
  ],
  "rate": "3.95",
  "comment_count": "191",
  "poster_url": "https://.../cover.jpg",
  "fanart_urls": ["https://.../sample1.jpg", "https://.../sample2.jpg"],
  "trailer_url": "https://.../preview.mp4",
  "actors": [{"name": "真北祈", "href": "/actors/450wJ", "gender": "female"}],
  "lead_actor": {"name": "真北祈", "href": "/actors/450wJ", "gender": "female"},
  "supporting_actors": [{"name": "マッスル澤野", "href": "...", "gender": "male"}],
  "magnets": [
    {
      "href": "magnet:?xt=urn:btih:...",
      "name": "VDD-201.torrent",
      "tags": ["字幕", "HD"],
      "size": "4.94GB",
      "timestamp": "2026-02-10"
    }
  ],
  "review_count": 4,
  "want_count": 1030,
  "watched_count": 191,
  "parse_success": true
}
```

### 2.5 Parsing Category Pages

```bash
curl -X POST http://localhost:8100/api/parse/category \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**Response:** Same structure as index pages, with additional `category_type` and `category_name` fields.

### 2.6 Parsing Ranking Pages

```bash
curl -X POST http://localhost:8100/api/parse/top \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**Response:** Same structure as index pages, with additional `top_type` and `period` fields. The `ranking` field on each movie is populated.

### 2.7 Parsing Tag Filter Pages

```bash
curl -X POST http://localhost:8100/api/parse/tags \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**Response example (key portions):**

```json
{
  "has_movie_list": true,
  "movies": [...],
  "current_selections": {"1": "23", "7": "28", "11": "2026"},
  "categories": [
    {
      "category_id": "4",
      "name": "體型",
      "options": [
        {"name": "熟女", "tag_id": "15", "selected": false},
        {"name": "巨乳", "tag_id": "17", "selected": false},
        {"name": "蘿莉塔", "tag_id": "19", "selected": false}
      ]
    },
    {
      "category_id": "7",
      "name": "類別",
      "options": [
        {"name": "單體作品", "tag_id": "28", "selected": true},
        {"name": "VR", "tag_id": "212", "selected": false},
        {"name": "4K", "tag_id": "347", "selected": false}
      ]
    }
  ]
}
```

### 2.8 Detecting Page Type

```bash
curl -X POST http://localhost:8100/api/detect-page-type \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>"}'
```

```json
{"page_type": "detail"}
```

---

## 3. Data Model Reference

All models are Python `dataclass` objects and support the `.to_dict()` method for conversion to dicts.

### Model Inheritance Hierarchy

```
IndexPageResult
├── CategoryPageResult   (+ category_type, category_name)
├── TopPageResult        (+ top_type, period)
└── TagPageResult        (+ categories, current_selections)
```

### Common Models

| Model | Fields | Description |
|-------|--------|-------------|
| `MovieLink` | `name`, `href` | Generic link (actors, directors, makers, etc.) |
| `MagnetInfo` | `href`, `name`, `tags`, `size`, `timestamp` | Magnet link |
| `MovieIndexEntry` | `href`, `video_code`, `title`, `rate`, `comment_count`, `release_date`, `tags`, `cover_url`, `page`, `ranking` | Movie entry on list pages |
| `MovieDetail` | See [Section 1.2](#12-parsing-movie-detail-pages) | Full detail page information |
| `TagOption` | `name`, `tag_id`, `selected` | Tag filter option |
| `TagCategory` | `category_id`, `name`, `options` | Tag filter category |

### REST API Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/parse/index` | Parse index page |
| `POST` | `/api/parse/detail` | Parse detail page |
| `POST` | `/api/parse/category` | Parse category page |
| `POST` | `/api/parse/top` | Parse ranking page |
| `POST` | `/api/parse/tags` | Parse tag filter page |
| `POST` | `/api/detect-page-type` | Detect page type |

All POST endpoints accept the following request body format:

```json
{
  "html": "Full HTML string",
  "page_num": 1
}
```
