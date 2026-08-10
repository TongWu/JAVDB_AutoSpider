# JavDB 登录

系统包含自动登录功能，用于维护自定义 URL 抓取（演员、标签等）所需的会话 cookie。

## 为什么需要这个

使用 `--url` 抓取自定义 URL 时，JavDB 要求有效的会话 cookie。没有它，你会遇到年龄验证或登录拦截。自动登录处理以下事项：
- 登录 JavDB
- 年龄验证
- 会话 cookie 提取和更新
- 验证码识别（通过 OpenAI 兼容 API 的 AI 视觉模型）

## 快速开始

### 1. 配置凭据

```python
# 在 config.py 中
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# 自动识别验证码所必需（登录是非交互式的）
GPT_API_URL = 'https://api.gpt.ge/v1/chat/completions'  # OpenAI 兼容端点
GPT_API_KEY = ''   # 你的 API 密钥
```

### 2. 运行登录

```bash
python3 -m apps.cli.login
```

脚本将执行以下操作：
1. 获取登录页并处理年龄验证
2. 下载验证码图片并用配置的 AI 视觉模型识别
3. 登录并提取会话 cookie（验证码识别错误时会重试）
4. 在 `config.py` 中更新 `JAVDB_SESSION_COOKIE`

### 3. 使用自定义 URL

```bash
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/RdEb4"
```

## 验证码处理

JavDB 在登录表单上显示一个扭曲的图片验证码。脚本通过把图片发送给一个
OpenAI 兼容的视觉模型（`GPT_API_URL` + `GPT_API_KEY`）来自动识别。**没有手动
输入的回退方式**——如果未配置 GPT API，登录无法进行。

### 工作原理

1. 脚本从登录页下载验证码图片。
2. 把图片连同"只返回字符"的提示词发送给配置的模型（`CAPTCHA_MODEL`）。
3. 对返回内容做清洗（剥离代码块 / 引号 / 空白，再转小写——JavDB 的比较是
   大小写敏感的，且答案为小写）。
4. 提交候选答案。识别错误时会用新验证码重试，最多 `LOGIN_MAX_RETRIES` 次。

### 为什么需要重试

JavDB 扭曲验证码的单次识别准确率本身就很低（在一次 720 次的压测中，最好的
模型也只有约 25%），因此脚本依赖重试：总成功率 `= 1 - (1 - p)^n`。当 `p≈0.25`
时，8 次重试可把登录成功率提升到约 90%。如果经常登录失败，调高
`LOGIN_MAX_RETRIES`。

### 模型选择

`CAPTCHA_MODEL` 接受你的端点提供的任意支持视觉的模型。已压测的选项（准确率在
第一梯队内统计上并无显著差异）：

| 模型 | 单次准确率 | 说明 |
|---|---|---|
| `qwen-vl-ocr`（默认） | ~25% | 最快、最便宜，输出干净 |
| `gpt-4o` | ~28% | 最高，但有时会把输出包在代码块里 |
| `gpt-4o-mini` | ~19% | 更便宜，准确率更低 |

> **推理模型**（如 `gpt-5-mini-high`）可用，但更慢且准确率并不更高。它们在
> 输出答案前会消耗数百个隐藏 token，因此 `CAPTCHA_MAX_TOKENS` 必须保持较高
> （≥2000），否则会返回空答案且 `finish_reason=length`。这就是默认值为 2000
> 而非 50 的原因。

## 配置

```python
# 必需
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# 由登录脚本自动更新
JAVDB_SESSION_COOKIE = ''

# GPT 验证码识别（自动登录必需）
GPT_API_URL = ''
GPT_API_KEY = ''

# 验证码识别调优（可选——下面是合理的默认值）
CAPTCHA_MODEL = 'qwen-vl-ocr'   # 你的端点提供的任意视觉模型
CAPTCHA_MAX_TOKENS = 2000       # 推理模型需保持 >=2000
LOGIN_MAX_RETRIES = 8           # 每次登录的重试次数（单次准确率低）

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

## 与 CI Runner 共享登录状态

GitHub Actions runner 通过 Proxy Coordinator 的 `GlobalLoginState` Durable
Object 共享会话 cookie：第一个登录成功的 runner 把 cookie 发布上去，其余
runner 直接采用并跳过自己的登录。该缓存**只能由一次成功的登录填充**——当
Cloudflare 拦住 CI 登录（验证码被拒、POST 返回 HTTP 403）时，没有任何东西被
发布，于是每次运行都从零重新登录。

要打破这个死循环，在本地运行 `python3 -m apps.cli.login`——本地可以用未被标记的 IP 并
解出验证码——登录成功后它会把 cookie 发布到协调器。之后的 CI 运行就能采用它
并跳过登录。

这需要先配置好协调器（与 spider 使用的是同一组值）：

```python
# In config.py
PROXY_COORDINATOR_URL = 'https://proxy-coordinator.<account>.workers.dev'
PROXY_COORDINATOR_TOKEN = 'your_shared_secret'
```

为让发布的 cookie 能绑定到某个 CI worker，请通过一个同样存在于 runner
`PROXY_POOL` 中的代理登录——把 `LOGIN_PROXY_NAME` 设为池中某个代理即可。当协
调器未配置或不可达时，发布步骤会被静默跳过；本地 cookie 与 `config.py` 更新
仍照常成功。

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
