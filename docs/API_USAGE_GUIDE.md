# JAVDB AutoSpider API 使用指南

本文档介绍如何通过 **Python 模块** 和 **REST API** 两种方式调用 JAVDB AutoSpider 的解析能力。

---

## 目录

- [安装依赖](#安装依赖)
- [一、Python 模块 API](#一python-模块-api)
  - [1.1 解析首页/索引页](#11-解析首页索引页)
  - [1.2 解析影片详情页](#12-解析影片详情页)
  - [1.3 解析分类页](#13-解析分类页)
  - [1.4 解析排行榜页](#14-解析排行榜页)
  - [1.5 解析标签筛选页](#15-解析标签筛选页)
  - [1.6 自动检测页面类型](#16-自动检测页面类型)
- [二、REST API](#二rest-api)
  - [2.1 启动服务](#21-启动服务)
  - [2.2 健康检查](#22-健康检查)
  - [2.3 解析首页/索引页](#23-解析首页索引页)
  - [2.4 解析影片详情页](#24-解析影片详情页)
  - [2.5 解析分类页](#25-解析分类页)
  - [2.6 解析排行榜页](#26-解析排行榜页)
  - [2.7 解析标签筛选页](#27-解析标签筛选页)
  - [2.8 检测页面类型](#28-检测页面类型)
- [三、数据模型参考](#三数据模型参考)

---

## 安装依赖

```bash
pip install -r requirements.txt
```

核心依赖：`beautifulsoup4`、`lxml`。REST API 额外需要：`fastapi`、`uvicorn`。

---

## 一、Python 模块 API

所有解析函数接收 **HTML 字符串**，返回 **结构化 dataclass 对象**。解析器不做任何业务过滤（不区分 phase1/phase2，不过滤字幕/日期标签），返回页面上的所有原始数据。

### 1.1 解析首页/索引页

解析任何包含影片列表的页面（首页、分类页、排行榜页通用）。

```python
from api.parsers import parse_index_page

# 读取 HTML 内容
with open('page.html', 'r', encoding='utf-8') as f:
    html = f.read()

result = parse_index_page(html, page_num=1)

# 基本信息
print(result.has_movie_list)  # True / False
print(result.page_title)      # 页面标题
print(len(result.movies))     # 影片数量

# 遍历每部影片
for movie in result.movies:
    print(f"番号: {movie.video_code}")
    print(f"标题: {movie.title}")
    print(f"评分: {movie.rate}")
    print(f"评价人数: {movie.comment_count}")
    print(f"发布日期: {movie.release_date}")
    print(f"标签: {movie.tags}")           # ['含中字磁鏈', '今日新種']
    print(f"封面: {movie.cover_url}")
    print(f"链接: {movie.href}")
    print(f"排名: {movie.ranking}")        # 仅排行榜页有值
    print()

# 转为 dict（方便序列化为 JSON）
data = result.to_dict()
```

**返回类型：** `IndexPageResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| `has_movie_list` | `bool` | 页面是否包含影片列表 |
| `movies` | `List[MovieIndexEntry]` | 所有影片条目 |
| `page_title` | `str` | 页面 `<title>` 文本 |

**MovieIndexEntry 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `href` | `str` | 影片详情页链接 |
| `video_code` | `str` | 番号 (如 `"STAR-123"`) |
| `title` | `str` | 影片标题 |
| `rate` | `str` | 评分 (如 `"4.47"`) |
| `comment_count` | `str` | 评价人数 (如 `"595"`) |
| `release_date` | `str` | 发布日期 (如 `"2026-02-11"`) |
| `tags` | `List[str]` | 页面标签 (如 `["含中字磁鏈", "今日新種"]`) |
| `cover_url` | `str` | 封面图片 URL |
| `page` | `int` | 所在页码 |
| `ranking` | `Optional[int]` | 排名（仅排行榜页有值） |

---

### 1.2 解析影片详情页

从影片详情页提取完整元数据。

```python
from api.parsers import parse_detail_page

with open('detail.html', 'r', encoding='utf-8') as f:
    html = f.read()

detail = parse_detail_page(html)

# ---- 基本信息 ----
print(f"标题: {detail.title}")
print(f"番号: {detail.video_code}")
print(f"番号前缀链接: {detail.code_prefix_link}")  # 如 /video_codes/VDD
print(f"时长: {detail.duration}")
print(f"发布日期: {detail.release_date}")

# ---- 关联实体 (MovieLink: name + href) ----
if detail.maker:
    print(f"片商: {detail.maker.name} ({detail.maker.href})")
if detail.publisher:
    print(f"發行商: {detail.publisher.name}")
if detail.series:
    print(f"系列: {detail.series.name}")
for d in detail.directors:
    print(f"导演: {d.name}")
for a in detail.actors:
    print(f"演员: {a.name} ({a.href})")
for t in detail.tags:
    print(f"类别标签: {t.name}")

# ---- 评分 ----
print(f"评分: {detail.rate}")
print(f"评价人数: {detail.comment_count}")
print(f"短评数: {detail.review_count}")
print(f"想看: {detail.want_count} 人")
print(f"看过: {detail.watched_count} 人")

# ---- 媒体资源 ----
print(f"海报: {detail.poster_url}")
print(f"剧照: {detail.fanart_urls}")      # 全尺寸图片 URL 列表
print(f"预告片: {detail.trailer_url}")

# ---- 磁力链接 ----
for m in detail.magnets:
    print(f"磁力: {m.name} | 大小: {m.size} | 标签: {m.tags} | 时间: {m.timestamp}")
    print(f"  链接: {m.href}")

# ---- 兼容旧接口 ----
actor_name = detail.get_first_actor_name()       # 第一个演员名
magnets_list = detail.get_magnets_as_legacy()     # List[dict] 格式
```

**返回类型：** `MovieDetail`

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | `str` | 影片标题 |
| `video_code` | `str` | 番号 |
| `code_prefix_link` | `str` | 番号前缀页链接 (如 `/video_codes/VDD`) |
| `duration` | `str` | 时长 |
| `release_date` | `str` | 发布日期 |
| `publisher` | `Optional[MovieLink]` | 發行商 |
| `maker` | `Optional[MovieLink]` | 片商 |
| `series` | `Optional[MovieLink]` | 系列 |
| `directors` | `List[MovieLink]` | 导演列表 |
| `tags` | `List[MovieLink]` | 类别标签列表 |
| `rate` | `str` | 评分 |
| `comment_count` | `str` | 评价人数 |
| `poster_url` | `str` | 海报 URL |
| `fanart_urls` | `List[str]` | 剧照 URL 列表 |
| `trailer_url` | `Optional[str]` | 预告片 URL |
| `actors` | `List[MovieLink]` | 演员列表 |
| `magnets` | `List[MagnetInfo]` | 磁力链接列表 |
| `review_count` | `int` | 短评数量 |
| `want_count` | `int` | 想看人数 |
| `watched_count` | `int` | 看过人数 |
| `parse_success` | `bool` | 是否成功找到磁力链接区域 |

---

### 1.3 解析分类页

解析片商、發行商、系列、导演、番号前缀、演员等分类页，额外提取分类信息。

```python
from api.parsers import parse_category_page

result = parse_category_page(html, page_num=1)

print(f"分类类型: {result.category_type}")   # 如 'makers', 'directors'
print(f"分类名称: {result.category_name}")   # 如 'PRESTIGE'
print(f"影片数量: {len(result.movies)}")

# movies 字段与 IndexPageResult 完全一致
for movie in result.movies:
    print(f"  {movie.video_code} - {movie.title}")
```

**返回类型：** `CategoryPageResult`（继承自 `IndexPageResult`）

| 额外字段 | 类型 | 说明 |
|----------|------|------|
| `category_type` | `str` | 分类类型 (`makers`, `publishers`, `series`, `directors`, `video_codes`, `actors`) |
| `category_name` | `str` | 分类显示名称 |

---

### 1.4 解析排行榜页

解析 Top250、每日/每周/每月排行榜等页面。

```python
from api.parsers import parse_top_page

result = parse_top_page(html, page_num=1)

print(f"排行榜类型: {result.top_type}")   # 'top250', 'top_movies', 'top_playback'
print(f"时间段: {result.period}")          # '2025', 'daily', 'weekly', 'monthly'

for movie in result.movies:
    print(f"  #{movie.ranking} {movie.video_code} - 评分: {movie.rate}")
```

**返回类型：** `TopPageResult`（继承自 `IndexPageResult`）

| 额外字段 | 类型 | 说明 |
|----------|------|------|
| `top_type` | `str` | 排行榜类型 |
| `period` | `Optional[str]` | 时间段 |

---

### 1.5 解析标签筛选页

解析 `/tags` 页面，提取完整的标签筛选面板（所有分类、所有标签选项、ID ↔ 名称映射）以及影片列表。

```python
from api.parsers import parse_tag_page

result = parse_tag_page(html, page_num=1)

# ---- 影片列表（与 IndexPageResult 一致）----
print(f"影片数: {len(result.movies)}")

# ---- 当前筛选状态 ----
print(f"当前选择: {result.current_selections}")
# 输出: {'1': '23', '5': '24', '6': '29', '7': '28', '11': '2026'}

# ---- 查看所有分类 ----
for cat in result.categories:
    print(f"\n分类 c{cat.category_id}: {cat.name}")
    for opt in cat.options:
        status = " [已选]" if opt.selected else ""
        id_info = f" (ID: {opt.tag_id})" if opt.tag_id else " (ID 未知)"
        print(f"  - {opt.name}{id_info}{status}")

# ---- 按分类 ID 查询 ----
cat4 = result.get_category_by_id('4')        # 體型
print(f"體型分类下共 {len(cat4.options)} 个标签")

# ---- 按分类名称查询 ----
cat = result.get_category_by_name('行爲')     # 行爲
print(f"行爲分类 ID: c{cat.category_id}")

# ---- 获取 ID → 名称 映射 ----
id_map = cat4.get_id_to_name_map()
print(id_map['15'])    # → '熟女'
print(id_map['17'])    # → '巨乳'

# ---- 获取 名称 → ID 映射（反向查询）----
name_map = cat4.get_name_to_id_map()
print(name_map['熟女'])  # → '15'

# ---- 获取所有分类所有标签的全局映射 ----
full_map = result.get_full_id_to_name_map()
print(full_map[('4', '15')])   # → '熟女'     (體型分类, ID 15)
print(full_map[('1', '23')])   # → '淫亂真實'  (主題分类, ID 23)
print(full_map[('7', '28')])   # → '單體作品'  (類別分类, ID 28)

# ---- 查看某分类的已选标签 ----
cat7 = result.get_category_by_id('7')
for sel in cat7.get_selected():
    print(f"已选: {sel.name} (ID: {sel.tag_id})")
```

**返回类型：** `TagPageResult`（继承自 `IndexPageResult`）

| 额外字段 | 类型 | 说明 |
|----------|------|------|
| `categories` | `List[TagCategory]` | 所有筛选分类 |
| `current_selections` | `dict` | 当前选中状态 `{category_id: "tag_ids"}` |

**TagCategory 字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `category_id` | `str` | 分类 ID (对应 URL 参数 `c{N}`) |
| `name` | `str` | 分类名称 (如 "主題", "體型") |
| `options` | `List[TagOption]` | 该分类下所有标签选项 |

**TagCategory 便捷方法：**

| 方法 | 返回 | 说明 |
|------|------|------|
| `get_id_to_name_map()` | `dict` | `{tag_id: name}` 映射 |
| `get_name_to_id_map()` | `dict` | `{name: tag_id}` 反向映射 |
| `get_selected()` | `List[TagOption]` | 当前已选标签 |

**标签分类 ID 对照表：**

| URL 参数 | 分类名 | 示例标签 |
|----------|--------|----------|
| `c10` | 基本 | 可播放(6), 含磁鏈(1), 含字幕(2) |
| `c11` | 年份 | 2026, 2025, 2024… |
| `c1` | 主題 | 淫亂真實(23), 出軌(51), 強姦(52) |
| `c2` | 角色 | 高中女生(1), 美少女(5), 已婚婦女 |
| `c3` | 服裝 | 眼鏡(3), 角色扮演(43), 制服 |
| `c4` | 體型 | 熟女(15), 巨乳(17), 蘿莉塔 |
| `c5` | 行爲 | 乳交(14), 中出(18), 多P(24) |
| `c6` | 玩法 | 捆綁(29), 凌辱, SM |
| `c7` | 類別 | 單體作品(28), VR(212), 4K(347) |
| `c9` | 時長 | lt-45, 45-90, 90-120, gt-120 |

> **注意：** 括号内的数字是 tag ID。当页面仅选中少数分类时，大部分标签的 ID 可从 HTML 中提取。当页面选中了多个分类时，部分标签的 `tag_id` 可能为空字符串。建议从选择较少的页面（如仅选中一个分类）提取完整映射。

---

### 1.6 自动检测页面类型

不确定 HTML 是什么类型的页面？让解析器自动检测。

```python
from api.parsers import detect_page_type

page_type = detect_page_type(html)
# 返回: 'index', 'detail', 'top250', 'top_movies', 'makers',
#       'publishers', 'series', 'directors', 'video_codes',
#       'actors', 'tags', 或 'unknown'
```

---

## 二、REST API

REST API 是 Python 模块 API 的薄封装层，使用 FastAPI 框架。所有解析端点接收 HTML 字符串，返回 JSON。

### 2.1 启动服务

```bash
# 开发模式（自动重载）
uvicorn api.server:app --reload --port 8100

# 生产模式
uvicorn api.server:app --host 0.0.0.0 --port 8100 --workers 4
```

启动后访问 `http://localhost:8100/docs` 查看自动生成的 Swagger 文档。

### 2.2 健康检查

```bash
curl http://localhost:8100/api/health
```

```json
{"status": "ok"}
```

### 2.3 解析首页/索引页

```bash
curl -X POST http://localhost:8100/api/parse/index \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**响应示例：**

```json
{
  "has_movie_list": true,
  "page_title": "JavDB",
  "movies": [
    {
      "href": "/v/ABC-123",
      "video_code": "ABC-123",
      "title": "影片标题...",
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

### 2.4 解析影片详情页

```bash
curl -X POST http://localhost:8100/api/parse/detail \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>"}'
```

**响应示例：**

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
  "actors": [{"name": "真野祈", "href": "/actors/..."}],
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

### 2.5 解析分类页

```bash
curl -X POST http://localhost:8100/api/parse/category \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**响应：** 与索引页相同结构，额外包含 `category_type` 和 `category_name` 字段。

### 2.6 解析排行榜页

```bash
curl -X POST http://localhost:8100/api/parse/top \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**响应：** 与索引页相同结构，额外包含 `top_type` 和 `period` 字段。影片的 `ranking` 字段有值。

### 2.7 解析标签筛选页

```bash
curl -X POST http://localhost:8100/api/parse/tags \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>", "page_num": 1}'
```

**响应示例（截取关键部分）：**

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

### 2.8 检测页面类型

```bash
curl -X POST http://localhost:8100/api/detect-page-type \
  -H "Content-Type: application/json" \
  -d '{"html": "<html>...</html>"}'
```

```json
{"page_type": "detail"}
```

---

## 三、数据模型参考

所有模型都是 Python `dataclass`，支持 `.to_dict()` 方法转换为 dict。

### 模型继承关系

```
IndexPageResult
├── CategoryPageResult   (+ category_type, category_name)
├── TopPageResult        (+ top_type, period)
└── TagPageResult        (+ categories, current_selections)
```

### 通用模型

| 模型 | 字段 | 说明 |
|------|------|------|
| `MovieLink` | `name`, `href` | 通用链接（演员、导演、片商等） |
| `MagnetInfo` | `href`, `name`, `tags`, `size`, `timestamp` | 磁力链接 |
| `MovieIndexEntry` | `href`, `video_code`, `title`, `rate`, `comment_count`, `release_date`, `tags`, `cover_url`, `page`, `ranking` | 列表页影片条目 |
| `MovieDetail` | 见 [1.2 节](#12-解析影片详情页) | 详情页完整信息 |
| `TagOption` | `name`, `tag_id`, `selected` | 标签筛选选项 |
| `TagCategory` | `category_id`, `name`, `options` | 标签筛选分类 |

### REST API 端点汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/parse/index` | 解析索引页 |
| `POST` | `/api/parse/detail` | 解析详情页 |
| `POST` | `/api/parse/category` | 解析分类页 |
| `POST` | `/api/parse/top` | 解析排行榜页 |
| `POST` | `/api/parse/tags` | 解析标签筛选页 |
| `POST` | `/api/detect-page-type` | 检测页面类型 |

所有 POST 端点的请求体格式：

```json
{
  "html": "完整 HTML 字符串",
  "page_num": 1
}
```
