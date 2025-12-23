# JavDB 自动爬虫

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
![JadvDB Daily Ingestion](https://cronitor.io/badges/9uDCq6/production/qJMA3fMzsCxqf9S3tKJ0BxkfoBk.svg)
[![codecov](https://codecov.io/gh/TongWu/JAVDB_AutoSpider/branch/main/graph/badge.svg)](https://codecov.io/gh/TongWu/JAVDB_AutoSpider)

一个全面的 Python 自动化系统,用于从 javdb.com 提取种子链接并自动添加到 qBittorrent。系统包括智能历史记录追踪、Git 集成、自动化流水线执行和重复下载预防功能。

[English](README.md) | 简体中文

## 功能特性

### 核心爬虫功能
- 从 `javdb.com/?vft=2` 到 `javdb.com/?page=5&vft=2` 实时获取数据
- 过滤同时包含"含中字磁鏈"和"今日新種"标签的条目(支持多种语言变体)
- 根据特定分类和优先级顺序提取磁力链接
- 将结果保存到 `reports/DailyReport/` 目录中的带时间戳的 CSV 文件
- 全面的日志记录,支持不同级别(INFO、WARNING、DEBUG、ERROR)
- 多页面处理,带进度跟踪
- 提取额外的元数据(演员、评分、评论数)

### 种子分类系统
- **字幕 (subtitle)**: 带有"Subtitle"标签的磁力链接
- **hacked**: 磁力链接按优先级排序:
  1. UC无码破解 (-UC.无码破解.torrent) - 最高优先级
  2. UC (-UC.torrent)
  3. U无码破解 (-U.无码破解.torrent)
  4. U (-U.torrent) - 最低优先级

### 双模式支持
爬虫支持两种运行模式:

#### 每日模式(默认)
- 使用基础 URL: `https://javdb.com/?vft=2`
- 将结果保存到 `reports/DailyReport/` 目录
- 默认检查历史记录以避免重复下载
- 在 qBittorrent 中使用"JavDB"分类

#### Ad Hoc 模式(自定义 URL)
- 通过 `--url` 参数激活,用于自定义 URL(演员、标签等)
- 将结果保存到 `reports/AdHoc/` 目录
- **现在默认检查历史记录**以跳过已下载的条目
- 使用 `--ignore-history` 重新下载所有内容
- 在 qBittorrent 中使用"Ad Hoc"分类
- 示例: `python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ"`

### qBittorrent 集成
- 自动读取当天的 CSV 文件
- 通过 Web UI API 连接 qBittorrent
- 使用适当的分类和设置添加种子
- 全面的日志记录和进度跟踪
- 详细的汇总报告

### qBittorrent 文件过滤器
- 自动过滤最近添加的种子中的小文件
- 可配置的最小文件大小阈值（默认：50MB）
- 为低于阈值的文件设置优先级为 0（不下载）
- 过滤 NFO 文件、样本、截图等
- 支持预览模式（dry-run）
- 可选的分类过滤
- 通过 GitHub Actions 定时执行（在每日摄取后 2 小时）

### 重复下载预防
- **自动下载检测**: 通过检查历史 CSV 文件自动识别哪些种子已被下载
- **下载指示器**: 在每日报告 CSV 文件中为已下载的种子添加 `[DOWNLOADED]` 前缀
- **跳过重复下载**: qBittorrent 上传器自动跳过带有 `[DOWNLOADED]` 指示器的种子
- **多种种子类型支持**: 支持四种类型: hacked_subtitle、hacked_no_subtitle、subtitle、no_subtitle
- **增强的历史跟踪**: 追踪每部电影的 create_date(首次发现)和 update_date(最新修改)

### Git 集成与流水线
- 自动化 Git 提交和推送功能
- 流水线执行过程中的增量提交
- 带结果和日志的电子邮件通知
- 完整的工作流自动化

### JavDB 自动登录
- 自动刷新会话 cookie
- 验证码处理(手动输入或 2Captcha API)
- 自动更新 config.py
- 支持需要认证的自定义 URL 爬取
- 详见 [JavDB 登录指南](utils/login/JAVDB_LOGIN_README.md)

### CloudFlare 绕过(可选)
- 集成 [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping)
- Request Mirroring 模式实现透明 CF 绕过
- 自动 cookie 缓存和管理
- 支持本地和远程代理设置
- 使用 `--use-cf-bypass` 标志启用

## 安装

1. 安装 Python 依赖:
```bash
pip install -r requirements.txt
```

2. (可选)如果要使用 SOCKS5 代理,安装 SOCKS5 代理支持:
```bash
pip install requests[socks]
```

3. 复制并编辑配置文件:
```bash
cp config.py.example config.py
```

4. (可选)如需 CloudFlare 绕过功能,安装并运行 [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) 服务:
```bash
# 详见下方 CloudFlare 绕过部分的设置说明
```

### Docker 安装方式(替代方案)

您也可以使用 Docker 容器运行应用程序,这样可以简化依赖管理和部署。

#### Docker 快速开始

1. **从 GitHub Container Registry 拉取镜像:**
```bash
docker pull ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

2. **准备配置文件:**
```bash
cp config.py.example config.py
cp env.example .env
# 编辑 config.py 填入你的配置
```

3. **运行容器:**
```bash
docker run -d \
  --name javdb-spider \
  --restart unless-stopped \
  -v $(pwd)/config.py:/app/config.py:ro \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/Ad\ Hoc:/app/Ad\ Hoc \
  -v $(pwd)/Daily\ Report:/app/Daily\ Report \
  --env-file .env \
  ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

#### 使用 Docker Compose(推荐)

1. **使用自动化构建脚本:**
```bash
./docker/docker-build.sh
```

或者手动操作:

```bash
# 准备配置文件
cp config.py.example config.py
cp env.example .env

# 构建并启动
docker-compose -f docker/docker-compose.yml build
docker-compose -f docker/docker-compose.yml up -d
```

2. **查看日志:**
```bash
docker-compose -f docker/docker-compose.yml logs -f
```

详细 Docker 文档请参考 [DOCKER_README.md](docs/DOCKER_README.md) 或 [DOCKER_QUICKSTART.md](docs/DOCKER_QUICKSTART.md)。

## 使用方法

### Docker 使用方式

如果您通过 Docker 安装,可以使用以下命令管理容器:

#### 基本命令

```bash
# 查看容器日志
docker logs -f javdb-spider

# 查看 cron 日志
docker exec javdb-spider tail -f /var/log/cron.log

# 手动运行爬虫
docker exec javdb-spider python scripts/spider.py --use-proxy

# 手动运行流水线
docker exec javdb-spider python pipeline.py

# 在容器内执行命令
docker exec -it javdb-spider bash

# 停止容器
docker stop javdb-spider

# 启动容器
docker start javdb-spider

# 重启容器
docker restart javdb-spider
```

#### 使用 Docker Compose

```bash
# 启动容器
docker-compose -f docker/docker-compose.yml up -d

# 停止容器
docker-compose -f docker/docker-compose.yml down

# 查看日志
docker-compose -f docker/docker-compose.yml logs -f

# 重启容器
docker-compose -f docker/docker-compose.yml restart

# 重新构建并启动
docker-compose -f docker/docker-compose.yml build --no-cache
docker-compose -f docker/docker-compose.yml up -d
```

#### 配置定时任务

编辑 `.env` 文件来配置定时任务:

```bash
# 爬虫每天凌晨 3:00 运行
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python scripts/spider.py --use-proxy >> /var/log/cron.log 2>&1

# 流水线每天凌晨 4:00 运行
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python pipeline.py >> /var/log/cron.log 2>&1
```

修改 `.env` 后,重启容器:
```bash
docker-compose -f docker/docker-compose.yml restart
```

### 单独运行脚本(本地安装)

**运行爬虫提取数据:**
```bash
python Javdb_Spider.py
```

**运行 qBittorrent 上传器:**
```bash
# 每日模式(默认)
python qbtorrent_uploader.py

# Ad hoc 模式(用于自定义 URL 爬取结果)
python qbtorrent_uploader.py --mode adhoc

# 为 qBittorrent API 请求使用代理
python qbtorrent_uploader.py --use-proxy
```

**运行 qBittorrent 文件过滤器(过滤小文件):**
```bash
# 默认: 过滤最近 2 天内添加的小于 50MB 的文件
python scripts/qb_file_filter.py --min-size 50

# 自定义阈值和天数
python scripts/qb_file_filter.py --min-size 100 --days 3

# 演练模式(预览而不实际更改)
python scripts/qb_file_filter.py --min-size 50 --dry-run

# 仅过滤特定分类
python scripts/qb_file_filter.py --min-size 50 --category JavDB

# 使用代理
python scripts/qb_file_filter.py --min-size 50 --use-proxy
```

**运行 PikPak 桥接器(将旧种子从 qBittorrent 转移到 PikPak):**
```bash
# 默认: 批量模式处理 3 天以上的种子
python pikpak_bridge.py

# 自定义天数阈值
python pikpak_bridge.py --days 7

# 演练模式(测试而不实际转移)
python pikpak_bridge.py --dry-run

# 单个模式(逐个处理种子而非批量)
python pikpak_bridge.py --individual

# 为 qBittorrent API 请求使用代理
python pikpak_bridge.py --use-proxy

# 组合选项
python pikpak_bridge.py --days 5 --dry-run --use-proxy
```

### 命令行参数

JavDB Spider 支持各种命令行参数进行自定义:

#### 基础选项
```bash
# 演练模式(不写入 CSV 文件)
python Javdb_Spider.py --dry-run

# 指定自定义输出文件名
python Javdb_Spider.py --output-file my_results.csv

# 自定义页面范围
python Javdb_Spider.py --start-page 3 --end-page 10

# 解析所有页面直到找到空页面
python Javdb_Spider.py --all
```

#### 阶段控制
```bash
# 仅运行阶段 1(字幕 + 今日/昨日标签)
python Javdb_Spider.py --phase 1

# 仅运行阶段 2(今日/昨日标签 + 质量过滤)
python Javdb_Spider.py --phase 2

# 运行两个阶段(默认)
python Javdb_Spider.py --phase all
```

#### 历史控制
```bash
# 忽略历史文件并爬取所有页面(用于每日和 ad hoc 模式)
python Javdb_Spider.py --ignore-history

# 自定义 URL 爬取(保存到 reports/AdHoc/,默认检查历史)
python Javdb_Spider.py --url "https://javdb.com/?vft=2"

# 自定义 URL 爬取,忽略历史重新下载所有内容
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-history

# 忽略今日/昨日发布日期标签,处理所有匹配条目
python Javdb_Spider.py --ignore-release-date

# 为所有 HTTP 请求使用代理
python Javdb_Spider.py --use-proxy
```

#### 完整示例
```bash
# 限制页面的快速测试运行
python Javdb_Spider.py --start-page 1 --end-page 3 --dry-run

# 忽略历史的完整爬取
python Javdb_Spider.py --all --ignore-history

# 带特定输出文件的自定义 URL
python Javdb_Spider.py --url "https://javdb.com/?vft=2" --output-file custom_results.csv

# 仅阶段 1 + 自定义页面范围
python Javdb_Spider.py --phase 1 --start-page 5 --end-page 15

# 下载所有字幕条目,无论发布日期
python Javdb_Spider.py --ignore-release-date --phase 1

# 下载所有高质量条目,无论发布日期
python Javdb_Spider.py --ignore-release-date --phase 2 --start-page 1 --end-page 10

# Ad hoc 模式: 下载特定演员的电影(跳过已下载)
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-release-date

# Ad hoc 模式: 重新下载演员的所有内容(忽略历史)
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-history --ignore-release-date

# 使用代理访问 JavDB(适用于地理限制地区)
python Javdb_Spider.py --use-proxy --start-page 1 --end-page 5

# 组合多个选项: 代理 + 自定义 URL + 忽略发布日期
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --use-proxy --ignore-release-date
```

#### 参数参考表

| 参数 | 描述 | 默认值 | 示例 |
|----------|-------------|---------|---------|
| `--dry-run` | 打印条目而不写入 CSV | False | `--dry-run` |
| `--output-file` | 自定义 CSV 文件名 | 自动生成 | `--output-file results.csv` |
| `--start-page` | 起始页码 | 1 | `--start-page 5` |
| `--end-page` | 结束页码 | 20 | `--end-page 10` |
| `--all` | 解析直到空页面 | False | `--all` |
| `--ignore-history` | 跳过历史检查(每日和 ad hoc) | False | `--ignore-history` |
| `--url` | 要爬取的自定义 URL(启用 ad hoc 模式) | None | `--url "https://javdb.com/?vft=2"` |
| `--phase` | 运行的阶段(1/2/all) | all | `--phase 1` |
| `--ignore-release-date` | 忽略今日/昨日标签 | False | `--ignore-release-date` |
| `--use-proxy` | 从 config.py 启用代理 | False | `--use-proxy` |
| `--use-cf-bypass` | 使用 CloudFlare 绕过服务 | False | `--use-cf-bypass` |

### 附加工具

**JavDB 自动登录(用于自定义 URL 爬取):**
```bash
# 当会话 cookie 过期或使用 --url 参数前运行
python3 javdb_login.py

# 脚本将:
# 1. 使用您的凭据登录 JavDB
# 2. 处理验证码(手动输入或 2Captcha API)
# 3. 提取并更新 config.py 中的会话 cookie
# 4. 验证 cookie 是否工作

# 详见上方 JavDB 自动登录部分的设置说明
```

**检查代理禁用状态:**
```bash
# 查看禁用记录
cat "Daily Report/proxy_bans.csv"

# 禁用信息也包含在流水线电子邮件报告中
```

**运行迁移脚本:**
```bash
cd migration

# 清理重复的历史条目
python3 cleanup_history_priorities.py

# 更新历史文件格式(从旧版本升级时)
python3 update_history_format.py

# 重新分类种子(分类规则更改后)
python3 reclassify_c_hacked_torrents.py
```

### 自动化流水线

**运行完整工作流:**
```bash
# 基础流水线运行
python pipeline_run_and_notify.py

# 带自定义参数的流水线(传递给 Javdb_Spider)
python pipeline_run_and_notify.py --start-page 1 --end-page 5

# 忽略发布日期标签的流水线
python pipeline_run_and_notify.py --ignore-release-date --phase 1

# 带自定义 URL 的流水线
python pipeline_run_and_notify.py --url "https://javdb.com/actors/EvkJ"

# 启用代理的流水线
python pipeline_run_and_notify.py --use-proxy

# 使用 PikPak 单个模式的流水线(逐个处理种子)
python pipeline_run_and_notify.py --pikpak-individual
```

流水线将:
1. 运行 JavDB Spider 提取数据(使用提供的参数)
2. 立即将爬虫结果提交到 GitHub
3. 运行 qBittorrent Uploader 添加种子
4. 立即将上传器结果提交到 GitHub
5. 运行 PikPak Bridge 处理旧种子(默认批量模式,使用 `--pikpak-individual` 为单个模式)
6. 执行最终提交并推送到 GitHub
7. **分析日志中的严重错误**
8. 发送带有适当状态的电子邮件通知

**注意**: 流水线接受与 `Javdb_Spider.py` 相同的参数并自动传递。额外的流水线特定参数包括 `--pikpak-individual` 用于 PikPak Bridge 模式控制。

#### 智能错误检测

流水线现在包含复杂的错误分析,可区分:

**严重错误(邮件标记为 FAILED):**
- 无法访问 JavDB 主站点(所有页面失败)
- 无法连接到 qBittorrent
- 无法登录 qBittorrent
- 所有种子添加失败
- 网络完全不可达

**非严重错误(邮件标记为 SUCCESS):**
- 某些特定 JavDB 页面失败(但主站点可访问)
- 某些单个种子添加失败(但 qBittorrent 可访问)
- PikPak API 问题(PikPak 服务问题,非基础设施问题)
- 未找到新种子(预期行为)

这确保您只在出现需要注意的真实基础设施问题时才收到 FAILED 邮件,而不仅仅是因为没有新内容或小问题。

## 配置

### 统一配置 (`config.py`)

所有配置设置现在集中在单个 `config.py` 文件中:

```python
# =============================================================================
# GIT 配置
# =============================================================================
GIT_USERNAME = 'your_github_username'
GIT_PASSWORD = 'your_github_token_or_password'
GIT_REPO_URL = 'https://github.com/your_username/your_repo_name.git'
GIT_BRANCH = 'main'

# =============================================================================
# QBITTORRENT 配置
# =============================================================================
QB_HOST = 'your_qbittorrent_ip'
QB_PORT = 'your_qbittorrent_port'
QB_USERNAME = 'your_qbittorrent_username'
QB_PASSWORD = 'your_qbittorrent_password'
TORRENT_CATEGORY = 'JavDB'  # 每日模式种子的分类
TORRENT_CATEGORY_ADHOC = 'Ad Hoc'  # adhoc 模式种子的分类
TORRENT_SAVE_PATH = ''
AUTO_START = True
SKIP_CHECKING = False
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_ADDITIONS = 1

# =============================================================================
# SMTP 配置(用于电子邮件通知)
# =============================================================================
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SMTP_USER = 'your_email@gmail.com'
SMTP_PASSWORD = 'your_email_password_or_app_password'
EMAIL_FROM = 'your_email@gmail.com'
EMAIL_TO = 'your_email@gmail.com'

# =============================================================================
# 代理配置
# =============================================================================

# 代理模式: 'single'(仅使用第一个代理)或 'pool'(自动故障转移)
PROXY_MODE = 'single'

# 代理池 - 代理列表(单一模式使用第一个,池模式使用全部)
PROXY_POOL = [
    {'name': 'Main-Proxy', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Backup-Proxy', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]

# 代理池行为(仅用于池模式)
PROXY_POOL_COOLDOWN_SECONDS = 691200  # 被禁代理的 8 天冷却期
PROXY_POOL_MAX_FAILURES = 3  # 冷却前的最大失败次数

# 传统代理配置(已弃用 - 请改用 PROXY_POOL)
PROXY_HTTP = None
PROXY_HTTPS = None

# 模块化代理控制 - 哪些模块使用代理
PROXY_MODULES = ['all']  # 'all' 或列表: 'spider_index', 'spider_detail', 'spider_age_verification', 'qbittorrent', 'pikpak'

# =============================================================================
# 爬虫配置
# =============================================================================
START_PAGE = 1
END_PAGE = 20
BASE_URL = 'https://javdb.com'

# 阶段 2 过滤标准
PHASE2_MIN_RATE = 4.0  # 阶段 2 条目的最低评分
PHASE2_MIN_COMMENTS = 80  # 阶段 2 条目的最少评论数

# 发布日期过滤器
IGNORE_RELEASE_DATE_FILTER = False  # 设为 True 以忽略今日/昨日标签

# 休眠时间配置(秒)
DETAIL_PAGE_SLEEP = 5  # 解析详情页前休眠
PAGE_SLEEP = 2  # 索引页之间休眠
MOVIE_SLEEP = 1  # 电影之间休眠

# =============================================================================
# JAVDB 登录配置(用于自动会话 cookie 刷新)
# =============================================================================

# JavDB 登录凭据(可选 - 用于自定义 URL 爬取)
JAVDB_USERNAME = ''  # 您的 JavDB 邮箱或用户名
JAVDB_PASSWORD = ''  # 您的 JavDB 密码

# 会话 cookie(由 javdb_login.py 自动更新)
JAVDB_SESSION_COOKIE = ''

# 可选: 2Captcha API 密钥用于自动验证码解决
# 从此处获取: https://2captcha.com/ (~$1/1000 个验证码)
TWOCAPTCHA_API_KEY = ''  # 留空以手动输入验证码

# =============================================================================
# CLOUDFLARE 绕过配置(可选)
# =============================================================================

# CloudFlare 绕过服务端口(必须匹配服务端口)
# 参见: https://github.com/sarperavci/CloudflareBypassForScraping
CF_BYPASS_SERVICE_PORT = 8000

# =============================================================================
# 日志配置
# =============================================================================
LOG_LEVEL = 'INFO'
SPIDER_LOG_FILE = 'logs/spider.log'
UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
PIPELINE_LOG_FILE = 'logs/pipeline.log'
EMAIL_NOTIFICATION_LOG_FILE = 'logs/email_notification.log'

# =============================================================================
# 文件路径
# =============================================================================
DAILY_REPORT_DIR = 'Daily Report'
AD_HOC_DIR = 'Ad Hoc'
PARSED_MOVIES_CSV = 'parsed_movies_history.csv'

# =============================================================================
# PIKPAK 配置(用于 PikPak Bridge)
# =============================================================================

# PikPak 登录凭据
PIKPAK_EMAIL = 'your_pikpak_email@example.com'
PIKPAK_PASSWORD = 'your_pikpak_password'

# PikPak 设置
PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'
PIKPAK_REQUEST_DELAY = 3  # 请求之间的延迟(秒)以避免速率限制

# =============================================================================
# qBittorrent 文件过滤器配置
# =============================================================================

# 最小文件大小阈值(MB)
# 小于此值的文件将被设置为"不下载"优先级
# 这有助于过滤 NFO 文件、样本、截图等小文件
QB_FILE_FILTER_MIN_SIZE_MB = 50

# 文件过滤器的日志文件
QB_FILE_FILTER_LOG_FILE = 'logs/qb_file_filter.log'
```

**设置说明:**
1. 将 `config.py.example` 复制为 `config.py`
2. 用您的实际凭据更新所有占位符值
3. `config.py` 文件出于安全原因自动排除在 git 提交之外

**GitHub 认证设置:**
1. 转到 GitHub Settings → Developer settings → Personal access tokens
2. 生成具有 `repo` 权限的新令牌
3. 使用此令牌作为 `GIT_PASSWORD`

**qBittorrent 设置:**
1. 在 qBittorrent 设置中启用 Web UI
2. 记下 IP 地址、端口、用户名和密码
3. 更新 `config.py` 中的 qBittorrent 配置部分

**电子邮件设置(可选):**
1. 对于 Gmail,使用应用专用密码而不是常规密码
2. 启用双因素认证并生成应用专用密码
3. 更新 `config.py` 中的 SMTP 配置部分

## 输出结构

### CSV 文件列
爬虫生成的 CSV 文件包含以下列:
- `href`: 视频页面 URL
- `video-title`: 视频标题
- `page`: 找到条目的页码
- `actor`: 主要演员名称
- `rate`: 评分分数
- `comment_number`: 用户评论/评分数量
- `hacked_subtitle`: 带字幕的破解版本磁力链接
- `hacked_no_subtitle`: 无字幕的破解版本磁力链接
- `subtitle`: 字幕版本磁力链接
- `no_subtitle`: 常规版本磁力链接(优先 4K,如果可用)
- `size_hacked_subtitle`, `size_hacked_no_subtitle`, `size_subtitle`, `size_no_subtitle`: 对应大小

### 文件位置

所有报告文件都在 `reports/` 目录下:

```
reports/
├── DailyReport/YYYY/MM/    # 每日报告 CSV 文件
│   └── Javdb_TodayTitle_YYYYMMDD.csv
├── AdHoc/YYYY/MM/          # Ad Hoc 报告 CSV 文件
│   └── Javdb_AdHoc_*.csv
├── parsed_movies_history.csv    # 历史记录
├── pikpak_bridge_history.csv    # PikPak 传输历史
└── proxy_bans.csv               # 代理禁用记录
```

- **每日报告 CSV 文件**: `reports/DailyReport/YYYY/MM/Javdb_TodayTitle_YYYYMMDD.csv`
- **Ad Hoc CSV 文件**: `reports/AdHoc/YYYY/MM/Javdb_AdHoc_*.csv`
- **历史文件**: `reports/parsed_movies_history.csv`
- **PikPak 历史**: `reports/pikpak_bridge_history.csv`
- **代理禁用记录**: `reports/proxy_bans.csv`
- **日志文件**: `logs/` 目录
  - `spider.log`
  - `qb_uploader.log`
  - `pipeline.log`

## 历史系统

爬虫包含一个智能历史系统,跟踪每部电影找到的种子类型:

### 多种种子类型跟踪
- 跟踪每部电影的所有可用种子类型(例如,同时跟踪 `hacked_subtitle` 和 `subtitle`)
- 当电影已有完整的种子集合时防止冗余处理
- 仅搜索基于偏好规则缺失的种子类型

### 处理规则
- **阶段 1**: 根据偏好处理缺少种子类型的电影
- **阶段 2**: 仅处理可从 `no_subtitle` 升级到 `hacked_no_subtitle` 或符合质量标准的电影
- **新电影**: 无论历史记录如何都始终处理

### 阶段 2 质量过滤
阶段 2 包含基于用户评分和评论数的可配置质量过滤:
- **最低评分**: 通过 `PHASE2_MIN_RATE` 配置(默认: 4.0)
- **最少评论数**: 通过 `PHASE2_MIN_COMMENTS` 配置(默认: 80)
- **目的**: 确保仅在阶段 2 处理高质量内容

### 偏好规则
- **破解分类**: 始终偏好 `hacked_subtitle` 而非 `hacked_no_subtitle`
- **字幕分类**: 始终偏好 `subtitle` 而非 `no_subtitle`
- **完整集合目标**: 每部电影应该有两个分类的代表

### 发布日期过滤

默认情况下,爬虫根据发布日期标签("今日新種"或"昨日新種")过滤条目。您可以通过两种方式覆盖此行为:

#### 命令行参数(推荐)
```bash
# 单次运行忽略发布日期标签
python Javdb_Spider.py --ignore-release-date

# 或通过流水线
python pipeline_run_and_notify.py --ignore-release-date
```

#### 配置文件
在 `config.py` 中设置 `IGNORE_RELEASE_DATE_FILTER = True` 以永久忽略发布日期标签。

**使用 `--ignore-release-date` 或 `IGNORE_RELEASE_DATE_FILTER = True` 时的行为:**
- **阶段 1**: 下载所有带字幕标签的条目,无论发布日期
- **阶段 2**: 下载所有符合质量标准(评分 > 4.0,评论数 > 80)的条目,无论发布日期

这在以下情况下很有用:
- 您想用旧内容回填您的收藏
- 您正在爬取自定义 URL(如演员页面),发布日期不相关
- 您想下载所有符合质量标准的内容

### 代理支持

系统支持**单一代理**和**代理池**模式以提高可靠性:

#### 代理池模式(✨ 新功能 - 推荐)

配置多个代理进行自动故障转移:
- **自动切换**: 当一个代理失败时,自动切换到另一个
- **被动健康检查**: 仅在实际失败时标记代理为失败(无主动探测)
- **冷却机制**: 失败的代理暂时禁用以允许恢复(默认 8 天)
- **禁用检测**: 自动检测代理何时被 JavDB 禁用
- **持久化禁用记录**: 禁用历史存储在 `Daily Report/proxy_bans.csv` 中,跨运行持久化
- **统计跟踪**: 每个代理的详细成功率和使用统计
- **完美适配 JavDB**: 尊重严格的速率限制,同时提供冗余

详见 [PROXY_POOL_GUIDE.md](PROXY_POOL_GUIDE.md) 获取详细配置和使用指南。

**快速设置:**
```python
# 在 config.py 中
PROXY_MODE = 'pool'
PROXY_POOL = [
    {'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Proxy-2', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]
PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 天冷却期(JavDB 禁止 7 天)
PROXY_POOL_MAX_FAILURES = 3  # 冷却前的最大失败次数
```

**代理禁用管理:**

系统包含智能禁用检测和管理:
- **自动检测**: 检测 JavDB 何时阻止代理 IP
- **持久化记录**: 禁用历史存储在 `Daily Report/proxy_bans.csv`
- **8 天冷却期**: 默认冷却期匹配 JavDB 的 7 天禁止期
- **退出代码 2**: 检测到代理被禁时,爬虫以代码 2 退出(有助于自动化)
- **禁用摘要**: 流水线电子邮件报告中包含详细的禁用状态

**检查禁用状态:**
```bash
# 禁用记录记录在:
cat "Daily Report/proxy_bans.csv"

# 流水线邮件包含禁用摘要,包括:
# - 代理名称和 IP
# - 禁用时间戳
# - 冷却到期时间
# - 当前状态(BANNED/AVAILABLE)
```

然后使用 `--use-proxy` 标志运行:
```bash
python Javdb_Spider.py --use-proxy
```

#### 单一代理模式(传统)

爬虫还支持传统的单一代理配置,用于 HTTP/HTTPS/SOCKS5 代理。这在以下情况下很有用:
- JavDB 在您的地区受到地理限制
- 您需要通过特定网络路由流量
- 您想使用 VPN 或代理服务

#### 设置

**1. 在 `config.py` 中配置代理:**
```python
# HTTP/HTTPS 代理
PROXY_HTTP = 'http://127.0.0.1:7890'
PROXY_HTTPS = 'http://127.0.0.1:7890'

# 或 SOCKS5 代理
PROXY_HTTP = 'socks5://127.0.0.1:1080'
PROXY_HTTPS = 'socks5://127.0.0.1:1080'

# 带认证
PROXY_HTTP = 'http://username:password@proxy.example.com:8080'
PROXY_HTTPS = 'http://username:password@proxy.example.com:8080'

# 控制哪些模块使用代理(模块化控制)
PROXY_MODULES = ['all']  # 为所有模块启用
# PROXY_MODULES = ['spider_index', 'spider_detail']  # 仅索引和详情页
# PROXY_MODULES = ['spider_detail']  # 仅详情页
# PROXY_MODULES = []  # 为所有模块禁用
```

**2. 使用命令行标志启用代理:**
```bash
# 为爬虫启用代理
python Javdb_Spider.py --use-proxy

# 为 qBittorrent 上传器启用代理
python qbtorrent_uploader.py --use-proxy

# 为 PikPak 桥接器启用代理
python pikpak_bridge.py --use-proxy

# 与其他选项组合
python Javdb_Spider.py --use-proxy --url "https://javdb.com/actors/EvkJ"

# 通过流水线(为所有组件启用代理)
python pipeline_run_and_notify.py --use-proxy
```

**注意:** 
- 代理**默认禁用**。您必须使用 `--use-proxy` 来启用它。
- 如果设置了 `--use-proxy` 但 `config.py` 中未配置代理,将记录警告。
- 您可以通过 `PROXY_MODULES` 配置控制爬虫的哪些部分使用代理。

#### 模块化代理控制

`PROXY_MODULES` 设置允许对哪些部分使用代理进行细粒度控制:

| 模块 | 描述 | 使用场景 |
|--------|-------------|----------|
| `spider_index` | 索引/列表页面 | 使用代理访问主列表页面 |
| `spider_detail` | 电影详情页面 | 使用代理访问单个电影页面 |
| `spider_age_verification` | 年龄验证绕过 | 使用代理进行年龄验证请求 |
| `qbittorrent` | qBittorrent Web UI API | 使用代理进行 qBittorrent API 请求 |
| `pikpak` | PikPak 桥接器 qBittorrent API | 使用代理进行 PikPak 桥接操作 |
| `all` | 所有模块 | 为所有内容使用代理(默认) |

**示例:**
```python
# 为所有内容使用代理
PROXY_MODULES = ['all']

# 仅为详情页使用代理(在索引页上节省带宽)
PROXY_MODULES = ['spider_detail']

# 为索引和详情使用代理,但不为年龄验证
PROXY_MODULES = ['spider_index', 'spider_detail']

# 仅为 qBittorrent 和 PikPak 使用代理,不为爬虫
PROXY_MODULES = ['qbittorrent', 'pikpak']

# 仅为爬虫使用代理,不为 qBittorrent/PikPak
PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']

# 为所有模块禁用代理(即使设置了 --use-proxy)
PROXY_MODULES = []
```

**常见场景:**
- **仅 JavDB 受地理限制**: `PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']`
- **本地 qBittorrent 在防火墙后**: `PROXY_MODULES = ['qbittorrent', 'pikpak']`
- **通过代理的所有内容**: `PROXY_MODULES = ['all']`

#### 支持的代理类型
- **HTTP**: `http://proxy.example.com:8080`
- **HTTPS**: `https://proxy.example.com:8080`
- **SOCKS5**: `socks5://proxy.example.com:1080`(需要 `requests[socks]` 包)

#### 安装 SOCKS5 支持
如果您想使用 SOCKS5 代理,安装额外的依赖:
```bash
pip install requests[socks]
```

#### 代理问题排查

**错误: 500 Internal Server Error**
- 检查代理服务器是否正在运行且可访问
- 验证代理凭据(用户名/密码)
- 如果密码包含特殊字符,进行 URL 编码:
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')
  print(encoded)  # 输出: My%40Pass%21
  ```
- 手动测试代理:
  ```bash
  curl -x http://username:password@proxy:port https://javdb.com
  ```

**错误: Connection refused 或 timeout**
- 检查代理服务器是否正在运行: `telnet proxy_ip proxy_port`
- 验证防火墙规则允许连接到代理
- 检查代理是否需要认证

**代理工作但下载失败**
- 某些代理不支持磁力链接或种子
- 尝试不同的代理或为 qBittorrent/PikPak 使用直接连接:
  ```python
  PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']
  ```

**带特殊字符的密码**
需要 URL 编码的常见特殊字符:
- `@` → `%40`
- `:` → `%3A`(仅在密码中,不在 `@` 后)
- `/` → `%2F`
- `?` → `%3F`
- `#` → `%23`
- `&` → `%26`
- `=` → `%3D`
- `+` → `%2B`
- Space → `%20`
- `!` → `%21`

示例: `http://user:My@Pass!123@proxy:8080` 变为 `http://user:My%40Pass%21123@proxy:8080`

### CloudFlare 绕过支持

系统支持与 [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) 集成,用于处理 JavDB 上的 CloudFlare 保护。

#### 什么是 CloudFlare 绕过?

CloudFlare 绕过是一个可选功能,帮助您在启用 CloudFlare 保护时访问 JavDB。它使用 CloudflareBypassForScraping 服务,该服务自动:
- 处理 CloudFlare 挑战
- 管理 cf_clearance cookies
- 提供透明的请求转发(Request Mirroring 模式)

#### 设置

**1. 安装 CloudflareBypassForScraping:**

```bash
# 克隆仓库
git clone https://github.com/sarperavci/CloudflareBypassForScraping.git
cd CloudflareBypassForScraping

# 安装依赖
pip install -r requirements.txt

# 配置(如需要,编辑 config.json)
# 默认端口是 8000
```

**2. 启动 CF 绕过服务:**

```bash
# 本地设置(默认)
python app.py

# 自定义端口(在 config.py 中更新 CF_BYPASS_SERVICE_PORT 以匹配)
python app.py --port 8000
```

**3. 配置爬虫:**

编辑 `config.py` 设置 CF 绕过服务端口:

```python
# CloudFlare 绕过配置
CF_BYPASS_SERVICE_PORT = 8000  # 必须匹配服务端口
```

**4. 使用 CF 绕过运行爬虫:**

```bash
# 为爬虫启用 CF 绕过
python Javdb_Spider.py --use-cf-bypass

# 与代理组合
python Javdb_Spider.py --use-proxy --use-cf-bypass

# 通过流水线
python pipeline_run_and_notify.py --use-cf-bypass
```

#### 工作原理

启用 `--use-cf-bypass` 时:
1. **请求镜像**: 所有请求都通过 CF 绕过服务转发
2. **URL 重写**: 原始 URL `https://javdb.com/page` → `http://localhost:8000/page`
3. **Host 头**: 原始主机名通过 `x-hostname` 头发送
4. **Cookie 管理**: CF 绕过服务自动处理 cf_clearance cookies
5. **透明**: 您的爬虫代码不需要任何更改

#### 网络拓扑

**本地设置:**
```
爬虫 → http://localhost:8000 → CloudFlare 绕过服务 → https://javdb.com
```

**使用代理:**
```
爬虫 → http://proxy_ip:8000 → 代理服务器上的 CF 绕过 → https://javdb.com
```

使用代理池时,CF 绕过服务 URL 自动调整以匹配当前代理 IP。

#### 配置

```python
# 在 config.py 中
CF_BYPASS_SERVICE_PORT = 8000  # CF 绕过服务端口(默认: 8000)
```

**服务位置逻辑:**
- **无代理**: 使用 `http://localhost:8000`
- **使用代理池**: 使用 `http://{proxy_ip}:8000`(从当前代理 URL 提取 IP)

这允许您在与代理相同的服务器上运行 CF 绕过服务以获得更好的性能。

#### 何时使用

在以下情况下使用 CloudFlare 绕过:
- ✅ JavDB 显示 CloudFlare 挑战页面
- ✅ 您收到"Access Denied"或"Checking your browser"错误
- ✅ 在浏览器中直接访问有效但在脚本中失败
- ✅ 仅代理无法绕过 CloudFlare 保护

#### 故障排查

**错误: "Connection refused to localhost:8000"**
- 确保 CF 绕过服务正在运行
- 检查端口 8000 是否可用: `netstat -an | grep 8000`
- 如果使用不同端口,更新 `CF_BYPASS_SERVICE_PORT`

**错误: "No movie list found" with CF bypass**
- 检查 CF 绕过服务日志是否有错误
- 验证 `x-hostname` 头是否正确发送
- 尝试重启 CF 绕过服务

**CF 绕过 + 代理不工作**
- 确保 CF 绕过服务在代理服务器上运行
- 验证代理 IP 提取是否正确(检查日志)
- 直接测试 CF 绕过服务: `curl http://proxy_ip:8000/`

#### 性能说明

- **首次请求**: 较慢(CF 挑战解决)
- **后续请求**: 快速(cookie 已缓存)
- **Cookie TTL**: 变化(通常为几小时到几天)
- **开销**: 首次请求后最小

### JavDB 自动登录

系统包含自动登录功能,用于维护自定义 URL 爬取的会话 cookies。

#### 为什么使用自动登录?

使用 `--url` 参数爬取自定义 URL(演员、标签等)时,JavDB 需要有效的会话 cookie。此 cookie 在一段时间后过期,导致年龄验证或登录问题失败。

自动登录通过以下方式解决此问题:
- ✅ 自动登录 JavDB
- ✅ 自动处理年龄验证
- ✅ 提取和更新会话 cookie
- ✅ 支持验证码(手动输入或 2Captcha API)

#### 快速开始

**1. 在 `config.py` 中配置凭据:**

```python
# JavDB 登录凭据(用于自动会话 cookie 刷新)
JAVDB_USERNAME = 'your_email@example.com'  # 或用户名
JAVDB_PASSWORD = 'your_password'

# 可选: 2Captcha API 密钥用于自动验证码解决
TWOCAPTCHA_API_KEY = ''  # 留空以手动输入验证码
```

**2. 运行登录脚本:**

```bash
python3 javdb_login.py
```

**3. 在提示时输入验证码:**

脚本将:
- 下载并保存验证码图像到 `javdb_captcha.png`
- 自动打开图像(如果可能)
- 提示您输入验证码

**4. 使用自定义 URL 运行爬虫:**

```bash
# 带自定义 URL 的爬虫
python3 Javdb_Spider.py --url "https://javdb.com/actors/RdEb4"

# 带自定义 URL 的流水线
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"
```

#### 验证码处理

**手动输入(默认):**
1. 脚本下载验证码图像
2. 自动打开图像(取决于平台)
3. 在提示时输入代码
4. 简单且免费

**2Captcha API(可选):**
1. 在 [2Captcha](https://2captcha.com/) 注册
2. 将 API 密钥添加到 `config.py`: `TWOCAPTCHA_API_KEY = 'your_key'`
3. 脚本自动解决验证码(~$1/1000 个验证码)
4. 完全自动化但需要花钱

#### 配置选项

```python
# 在 config.py 中

# 登录凭据(必需)
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# 会话 cookie(由 javdb_login.py 自动更新)
JAVDB_SESSION_COOKIE = ''

# 可选: 2Captcha API 密钥
TWOCAPTCHA_API_KEY = ''  # 用于自动验证码解决

# 可选: 手动 cookie 提取
# 从浏览器 DevTools → Application → Cookies → _jdb_session 获取
# JAVDB_SESSION_COOKIE = 'your_session_cookie_here'
```

#### 何时重新运行

在以下情况下重新运行 `python3 javdb_login.py`:
- ✅ 会话 cookie 过期(通常几天/几周后)
- ✅ 爬虫在有效 URL 上显示"No movie list found"
- ✅ 出现年龄验证或登录错误
- ✅ 首次使用 `--url` 参数前

#### 自动化(可选)

**Cron Job (Linux/Mac):**
```bash
# 每 7 天刷新 cookie
0 0 */7 * * cd ~/JAVDB_AutoSpider && python3 javdb_login.py >> logs/javdb_login.log 2>&1
```

**任务计划程序(Windows):**
- 设置计划任务每周运行 `javdb_login.py`

#### 高级: 基于 OCR 的验证码解决

脚本在 `utils/login/javdb_captcha_solver.py` 中包含可选的基于 OCR 的验证码解决器:

```python
# 免费方法(包含)
solve_captcha(image_data, method='ocr')      # 本地 OCR(Tesseract)
solve_captcha(image_data, method='manual')   # 手动输入

# 付费方法(需要 API 密钥)
solve_captcha(image_data, method='2captcha') # 2Captcha API
solve_captcha(image_data, method='auto')     # 先尝试 OCR,回退到 2Captcha
```

**安装 Tesseract OCR(可选):**
```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# macOS
brew install tesseract

# Windows
# 从此处下载安装程序: https://github.com/UB-Mannheim/tesseract/wiki
```

#### 故障排查

**登录失败 - 验证码不正确:**
- 验证码区分大小写
- 再次尝试获取新验证码
- 考虑使用 2Captcha API

**登录失败 - 凭据无效:**
- 验证 config.py 中的用户名/密码
- 先在浏览器中测试凭据
- 检查拼写错误

**会话 Cookie 不工作:**
- 验证 cookie 在 config.py 中已更新
- 登录和爬虫使用相同的代理/网络
- 尝试再次登录

**有关详细故障排查和手动 cookie 提取,请参见 [JavDB 登录指南](utils/login/JAVDB_LOGIN_README.md)。**

## 下载指示器功能

系统包含高级重复下载预防功能,自动标记已下载的种子并在将来的运行中跳过它们。

### 功能概述

此功能在每日报告中实现已下载种子的自动标记,并在 qBittorrent 上传器中跳过这些已下载的种子以避免重复下载。系统还包含带有创建和更新时间戳的增强历史跟踪。

### 功能特性

1. **自动检测已下载种子**: 通过检查历史 CSV 文件自动识别哪些种子已被下载
2. **添加指示器**: 在每日报告 CSV 文件中为已下载的种子添加 `[DOWNLOADED]` 前缀
3. **跳过重复下载**: qBittorrent 上传器自动跳过带有 `[DOWNLOADED]` 指示器的种子
4. **支持多种种子类型**: 支持四种类型: hacked_subtitle、hacked_no_subtitle、subtitle、no_subtitle
5. **增强的历史跟踪**: 追踪每部电影的 create_date(首次发现)和 update_date(最新修改)

### 增强的历史格式

历史 CSV 文件现在使用增强格式,为每种种子类型使用单独的列:

**旧格式:**
```
href,phase,video_code,parsed_date,torrent_type
```

**新格式:**
```
href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
```

- `create_date`: 电影首次被发现和记录的时间
- `update_date`: 电影最后一次用新种子类型更新的时间
- `hacked_subtitle`: 带字幕的破解版本的下载日期(如果未下载则为空)
- `hacked_no_subtitle`: 无字幕的破解版本的下载日期(如果未下载则为空)
- `subtitle`: 字幕版本的下载日期(如果未下载则为空)
- `no_subtitle`: 常规版本的下载日期(如果未下载则为空)
- 为现有文件保持向后兼容性

### 工作流程

1. **每日报告生成**: 爬虫生成每日报告 CSV 文件
2. **历史检查**: 上传器启动时检查历史 CSV 文件
3. **添加指示器**: 为已下载的种子添加 `[DOWNLOADED]` 前缀
4. **跳过处理**: 读取 CSV 时跳过带有指示器的种子
5. **上传新种子**: 仅上传未下载的种子
6. **更新历史**: 找到新种子类型时,修改 update_date

### 示例输出

**修改前的 CSV:**
```
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,magnet:?xt=...,magnet:?xt=...
```

**修改后的 CSV:**
```
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,[DOWNLOADED] magnet:?xt=...,[DOWNLOADED] magnet:?xt=...
```

**历史文件格式:**
```
href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
/v/mOJnXY,1,IPZZ-574,2025-07-09 20:00:57,2025-07-09 20:05:30,2025-07-09 20:05:30,,2025-07-09 20:05:30,
```

**上传器日志:**
```
2025-07-09 22:09:23,182 - INFO - Adding downloaded indicators to CSV file...
2025-07-09 22:09:23,183 - INFO - Added downloaded indicators to Daily Report/Javdb_TodayTitle_20250709.csv
2025-07-09 22:09:23,183 - INFO - Found 0 torrent links in Daily Report/Javdb_TodayTitle_20250709.csv
2025-07-09 22:09:23,183 - INFO - Skipped 20 already downloaded torrents
```

### 重要说明

1. **历史文件依赖**: 功能依赖于 `Daily Report/parsed_movies_history.csv` 文件
2. **指示器格式**: 下载指示器格式为 `[DOWNLOADED] `(注意空格)
3. **向后兼容性**: 如果历史文件不存在,功能将优雅降级而不影响正常使用
4. **性能优化**: 历史检查使用高效的 CSV 读取,不会显著影响性能
5. **时间戳跟踪**: create_date 保持不变,而 update_date 随每次修改而变化
6. **种子类型合并**: 更新现有记录时,新种子类型与现有种子类型合并

### 迁移

系统自动处理从旧格式(`parsed_date`)到新格式(`create_date`, `update_date`)的迁移。现有文件自动转换,保持向后兼容性。

此功能确保系统稳定性和效率,避免重复下载,同时通过增强的时间戳管理维护全面的历史跟踪。

## 迁移脚本

`migration/` 目录包含用于维护和升级系统的实用脚本:

### 可用脚本

**cleanup_history_priorities.py**
- 从历史文件中删除重复条目
- 确保数据完整性
- 可安全多次运行

**update_history_format.py**
- 将旧历史格式迁移到新格式
- 将 `parsed_date` 转换为 `create_date`/`update_date`
- 自动向后兼容

**reclassify_c_hacked_torrents.py**
- 重新分类具有特定命名模式的种子
- 更新种子类型分类
- 分类规则更改后有用

### 何时使用

在以下情况下运行迁移脚本:
- ✅ 从旧版本升级时
- ✅ 历史文件显示重复条目
- ✅ 引入格式更改
- ✅ 需要数据清理

### 如何运行

```bash
cd migration
python3 cleanup_history_priorities.py
python3 update_history_format.py
python3 reclassify_c_hacked_torrents.py
```

**注意:** 运行迁移脚本前务必备份您的 `Daily Report/parsed_movies_history.csv`。

## 日志

系统提供全面的日志记录:
- **INFO**: 带跟踪的一般进度信息
- **WARNING**: 非关键问题
- **DEBUG**: 详细的调试信息
- **ERROR**: 严重错误

进度跟踪包括:
- `[Page 1/5]` - 页面级进度
- `[15/75]` - 跨所有页面的条目级进度
- `[1/25]` - qBittorrent 的上传进度

## 故障排查

### 常见问题

**爬虫问题:**
- **未找到条目**: 检查网站结构是否已更改
- **连接错误**: 验证互联网连接和网站可访问性
- **CSV 未生成**: 检查"Daily Report"目录是否存在

**qBittorrent 问题:**
- **无法连接**: 检查 qBittorrent 是否正在运行且已启用 Web UI
- **登录失败**: 验证配置中的用户名和密码
- **找不到 CSV 文件**: 先运行爬虫以生成 CSV 文件

**Git 问题:**
- **认证失败**: 验证用户名和令牌/密码
- **找不到仓库**: 检查仓库 URL 和访问权限
- **分支问题**: 确保分支存在于您的仓库中

**下载指示器问题:**
- **未添加指示器**: 检查历史文件是否存在且格式正确
- **上传器跳过太多种子**: 检查历史文件是否包含过时记录
- **导入错误**: 确保 `utils/history_manager.py` 文件存在
- **历史格式问题**: 确保历史文件具有正确的列结构和向后兼容性

**JavDB 登录问题:**
- **登录失败**: 检查 config.py 中的凭据
- **验证码错误**: 再次尝试获取新验证码,或使用 2Captcha API
- **Cookie 不工作**: 验证 cookie 在 config.py 中已更新,登录和爬虫使用相同代理
- **详见 [JavDB 登录指南](utils/login/JAVDB_LOGIN_README.md) 获取详细故障排查**

**CloudFlare 绕过问题:**
- **连接被拒绝**: 确保 CF 绕过服务正在运行
- **端口错误**: 验证 CF_BYPASS_SERVICE_PORT 匹配服务端口
- **未找到电影列表**: 检查 CF 绕过服务日志
- **代理 + CF 不工作**: 确保 CF 绕过服务在代理服务器上运行

**代理禁用问题:**
- **所有代理被禁**: 检查 `Daily Report/proxy_bans.csv` 查看禁用状态
- **爬虫以代码 2 退出**: 表示检测到代理禁用,等待冷却期或添加新代理
- **冷却不工作**: 默认为 8 天,如需要调整 PROXY_POOL_COOLDOWN_SECONDS
- **禁用误报**: 检查 JavDB 是否实际上可从代理 IP 访问

### 调试模式

要查看详细操作,您可以临时提高脚本中的日志级别:

```python
# 在 config.py 中
LOG_LEVEL = 'DEBUG'  # 显示详细的调试信息
```

## 安全说明

- **配置文件**: `config.py` 自动排除在 git 提交之外(检查 `.gitignore`)
- **永远不要提交凭据**: GitHub 令牌、密码、API 密钥应仅保留在 `config.py` 中
- **GitHub 认证**: 使用个人访问令牌而非密码
- **JavDB 凭据**: 仅本地存储在 `config.py` 中,除了传输到 JavDB 外从不传输
- **PikPak 凭据**: 存储在 `config.py` 中,仅用于 PikPak API
- **2Captcha API 密钥**: 可选,仅在配置为自动验证码解决时使用
- **代理密码**: 对密码中的特殊字符使用 URL 编码
- **会话 cookies**: 由登录脚本自动更新,一段时间后过期
- **敏感日志**: 流水线自动在日志和电子邮件中屏蔽敏感信息
- **环境变量(可选)**: 考虑用于生产部署
  ```python
  import os
  JAVDB_USERNAME = os.getenv('JAVDB_USER', '')
  JAVDB_PASSWORD = os.getenv('JAVDB_PASS', '')
  ```

## 注意事项

### 速率限制和延迟
- 系统包含请求之间的延迟以尊重服务器:
  - **详情页**: 5 秒(通过 `DETAIL_PAGE_SLEEP` 配置)
  - **索引页**: 2 秒(通过 `PAGE_SLEEP` 配置)
  - **电影**: 1 秒(通过 `MOVIE_SLEEP` 配置)
  - **qBittorrent 添加**: 1 秒(通过 `DELAY_BETWEEN_ADDITIONS` 配置)
  - **PikPak 请求**: 3 秒(通过 `PIKPAK_REQUEST_DELAY` 配置)

### 系统行为
- 系统使用适当的头部来模拟真实浏览器
- CSV 文件自动保存到"Daily Report"或"Ad Hoc"目录
- 流水线提供增量提交以实时监控进度
- 历史文件跟踪所有已下载的电影及其时间戳
- 退出代码 2 表示检测到代理禁用(对自动化有用)
- 日志自动屏蔽敏感信息(密码、令牌等)

### 文件结构
- **Daily Report/**: 包含每日爬取结果和历史
- **Ad Hoc/**: 包含自定义 URL 爬取结果
- **logs/**: 包含所有日志文件
  - `Javdb_Spider.log`: 爬虫执行日志
  - `qbtorrent_uploader.log`: 上传执行日志
  - `pipeline_run_and_notify.log`: 流水线执行日志
  - `qb_pikpak.log`: PikPak 桥接执行日志
  - `qb_file_filter.log`: 文件过滤器执行日志
  - `proxy_bans.csv`: 代理禁用历史(跨运行持久化)
- **migration/**: 包含数据库迁移脚本
- **utils/**: 实用工具模块(历史、解析器、代理池等)
- **utils/login/**: JavDB 登录相关文件和文档

## 快速参考

### 常用命令

```bash
# 基础每日爬取
python3 Javdb_Spider.py
python3 qbtorrent_uploader.py

# 完整自动化流水线
python3 pipeline_run_and_notify.py

# 使用代理爬取
python3 Javdb_Spider.py --use-proxy
python3 pipeline_run_and_notify.py --use-proxy

# 使用 CloudFlare 绕过爬取
python3 Javdb_Spider.py --use-cf-bypass
python3 pipeline_run_and_notify.py --use-proxy --use-cf-bypass

# 自定义 URL 爬取(需要登录)
python3 javdb_login.py  # 首次设置
python3 Javdb_Spider.py --url "https://javdb.com/actors/RdEb4"
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"

# 忽略发布日期爬取
python3 Javdb_Spider.py --ignore-release-date --phase 1
python3 pipeline_run_and_notify.py --ignore-release-date

# Ad hoc 模式
python3 Javdb_Spider.py --url "https://javdb.com/tags/xyz"
python3 qbtorrent_uploader.py --mode adhoc

# PikPak 桥接器
python3 pikpak_bridge.py  # 默认: 3 天,批量模式
python3 pikpak_bridge.py --days 7 --individual  # 自定义天数,单个模式

# qBittorrent 文件过滤器
python3 scripts/qb_file_filter.py --min-size 50  # 过滤 < 50MB 的文件
python3 scripts/qb_file_filter.py --min-size 100 --days 3 --dry-run  # 预览模式
```

### 配置文件

- **主配置**: `config.py`(从 `config.py.example` 复制)
- **历史文件**: `Daily Report/parsed_movies_history.csv`
- **禁用记录**: `Daily Report/proxy_bans.csv`
- **登录文档**: `utils/login/JAVDB_LOGIN_README.md`

### 重要链接

- [CloudFlare 绕过服务](https://github.com/sarperavci/CloudflareBypassForScraping)
- [2Captcha API](https://2captcha.com/)(可选,用于自动验证码解决)
- [JavDB 登录指南](utils/login/JAVDB_LOGIN_README.md)

## 贡献

欢迎贡献! 请随时提交问题或拉取请求。

## 许可证

本项目仅用于教育和个人用途。请尊重您爬取的网站的服务条款。

