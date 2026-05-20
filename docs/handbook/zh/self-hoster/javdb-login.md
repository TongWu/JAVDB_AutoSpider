# JavDB 登录

系统包含自动登录功能，用于维护自定义 URL 抓取（演员、标签等）所需的会话 cookie。

## 为什么需要这个

使用 `--url` 抓取自定义 URL 时，JavDB 要求有效的会话 cookie。没有它，你会遇到年龄验证或登录拦截。自动登录处理以下事项：
- 登录 JavDB
- 年龄验证
- 会话 cookie 提取和更新
- 验证码识别（手动、OCR 或基于 GPT）

## 快速开始

### 1. 配置凭据

```python
# 在 config.py 中
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# 可选：基于 GPT 的验证码识别
GPT_API_URL = ''   # GPT API 端点
GPT_API_KEY = ''   # GPT API 密钥
```

### 2. 运行登录

```bash
python3 -m apps.cli.login
```

脚本将执行以下操作：
1. 下载并显示验证码图片
2. 提示你输入验证码（如果配置了 GPT 则自动识别）
3. 登录并提取会话 cookie
4. 在 `config.py` 中更新 `JAVDB_SESSION_COOKIE`

### 3. 使用自定义 URL

```bash
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/RdEb4"
```

## 验证码处理

### 手动输入（默认）

1. 脚本下载验证码图片
2. 自动打开图片（取决于平台）
3. 你在提示时输入验证码

### 基于 GPT（推荐用于自动化）

在 `config.py` 中配置 `GPT_API_URL` 和 `GPT_API_KEY`。脚本将验证码图片发送到 GPT API 进行自动识别。

### OCR（Tesseract）

使用 Tesseract 进行本地 OCR。安装方式：

```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# Windows — download from https://github.com/UB-Mannheim/tesseract/wiki
```

### 识别方法

```python
# 位于 utils/login/javdb_captcha_solver.py
solve_captcha(image_data, method='manual')    # 手动输入
solve_captcha(image_data, method='ocr')       # 本地 Tesseract OCR
solve_captcha(image_data, method='2captcha')  # 2Captcha API（遗留）
solve_captcha(image_data, method='auto')      # 优先 OCR，失败后回退
```

## 配置

```python
# 必需
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# 由登录脚本自动更新
JAVDB_SESSION_COOKIE = ''

# GPT 验证码（推荐）
GPT_API_URL = ''
GPT_API_KEY = ''

# 登录策略（高级）
LOGIN_ATTEMPTS_PER_PROXY_LIMIT = 3
LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH = 2
LOGIN_VERIFICATION_URLS = []  # 用于验证会话有效性的 URL
```

## 何时重新运行

在以下情况下重新运行 `python3 -m apps.cli.login`：
- 会话 cookie 过期（通常在数天/数周后）
- 对有效 URL 爬虫显示 "No movie list found"
- 出现年龄验证或登录错误
- 首次使用 `--url` 之前

## 自动化

### 定时任务（Linux/Mac）

```bash
# 每 7 天刷新一次 cookie
0 0 */7 * * cd ~/JAVDB_AutoSpider_CICD && python3 -m apps.cli.login >> logs/javdb_login.log 2>&1
```

### GitHub Actions

`DailyIngestion.yml` 和 `AdHocIngestion.yml` 工作流包含登录步骤，会在每次运行前自动刷新会话 cookie。

## 手动提取 Cookie

如果自动登录失败，可以手动提取 cookie：

1. 在浏览器中打开 JavDB 并登录
2. 打开开发者工具 → Application → Cookies
3. 复制 `_jdb_session` cookie 值
4. 在 `config.py` 中设置：
   ```python
   JAVDB_SESSION_COOKIE = 'your_session_cookie_here'
   ```

## 故障排查

**登录失败 —— 验证码错误：**
- 验证码区分大小写
- 重试以获取新验证码
- 考虑配置基于 GPT 的识别

**登录失败 —— 凭据无效：**
- 确认 `config.py` 中的用户名/密码
- 先在浏览器中测试凭据

**会话 cookie 不工作：**
- 确认 cookie 已在 `config.py` 中更新
- 登录和爬虫使用相同的 proxy/网络
- 尝试重新登录

详细故障排查请参见 [JavDB Login README](../../../../utils/login/JAVDB_LOGIN_README.md)。
