/** UI copy for the config page (aligned with backend section / keys; MDC-NG-style settings). */

export const SECTION_TAB_LABELS: Record<string, string> = {
  "GIT CONFIGURATION": "Git",
  "QBITTORRENT CONFIGURATION": "qBittorrent",
  "SMTP CONFIGURATION": "邮件",
  "PROXY CONFIGURATION": "代理",
  "CLOUDFLARE BYPASS CONFIGURATION": "Cloudflare",
  "SPIDER CONFIGURATION": "爬虫",
  "JAVDB LOGIN CONFIGURATION": "JavDB 登录",
  "LOGGING CONFIGURATION": "日志",
  "PARSING CONFIGURATION": "解析",
  "FILE PATHS": "路径",
  "PIKPAK CONFIGURATION": "PikPak",
  "QBITTORRENT FILE FILTER CONFIGURATION": "种子过滤",
  "RCLONE CONFIGURATION": "Rclone",
  "STORAGE MODE": "存储",
  "DEDUP CONFIGURATION": "去重",
};

function humanizeKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Primary field titles (Chinese labels preferred for display). */
export const FIELD_LABELS: Record<string, string> = {
  GIT_USERNAME: "Git 用户名",
  GIT_PASSWORD: "Git 密码 / Token",
  GIT_REPO_URL: "仓库地址",
  GIT_BRANCH: "分支名",
  QB_HOST: "qBittorrent 地址",
  QB_PORT: "qBittorrent 端口",
  QB_USERNAME: "qBittorrent 用户名",
  QB_PASSWORD: "qBittorrent 密码",
  TORRENT_CATEGORY: "种子分类（定期）",
  TORRENT_CATEGORY_ADHOC: "种子分类（手动）",
  TORRENT_SAVE_PATH: "保存路径",
  AUTO_START: "添加后自动开始",
  SKIP_CHECKING: "跳过校验",
  REQUEST_TIMEOUT: "请求超时（秒）",
  DELAY_BETWEEN_ADDITIONS: "添加间隔（秒）",
  SMTP_SERVER: "SMTP 服务器",
  SMTP_PORT: "SMTP 端口",
  SMTP_USER: "SMTP 用户名",
  SMTP_PASSWORD: "SMTP 密码",
  EMAIL_FROM: "发件人",
  EMAIL_TO: "收件人",
  PROXY_MODE: "代理模式",
  PROXY_POOL: "代理池",
  PROXY_POOL_COOLDOWN_SECONDS: "代理冷却（秒）",
  PROXY_POOL_MAX_FAILURES: "最大失败次数",
  PROXY_HTTP: "HTTP 代理（只读）",
  PROXY_HTTPS: "HTTPS 代理（只读）",
  PROXY_MODULES: "走代理的模块（JSON）",
  CF_BYPASS_SERVICE_PORT: "绕过服务端口",
  CF_BYPASS_ENABLED: "启用 Cloudflare 绕过",
  START_PAGE: "起始页",
  END_PAGE: "结束页",
  PHASE2_MIN_RATE: "二阶段最低评分",
  PHASE2_MIN_COMMENTS: "二阶段最少评论数",
  BASE_URL: "JavDB 基础 URL",
  JAVDB_USERNAME: "JavDB 用户名",
  JAVDB_PASSWORD: "JavDB 密码",
  JAVDB_SESSION_COOKIE: "Session Cookie",
  GPT_API_URL: "验证码 GPT API 地址",
  GPT_API_KEY: "验证码 GPT API Key",
  PAGE_SLEEP: "翻页间隔（秒）",
  MOVIE_SLEEP_MIN: "影片间隔最小（秒）",
  MOVIE_SLEEP_MAX: "影片间隔最大（秒）",
  CF_TURNSTILE_COOLDOWN: "Turnstile 冷却（秒）",
  FALLBACK_COOLDOWN: "回退冷却（秒）",
  LOG_LEVEL: "日志级别",
  SPIDER_LOG_FILE: "爬虫日志路径",
  UPLOADER_LOG_FILE: "上传器日志路径",
  PIPELINE_LOG_FILE: "流水线日志路径",
  EMAIL_NOTIFICATION_LOG_FILE: "邮件通知日志路径",
  IGNORE_RELEASE_DATE_FILTER: "忽略发行日过滤",
  INCLUDE_DOWNLOADED_IN_REPORT: "报告含已下载项",
  REPORTS_DIR: "报告根目录",
  DAILY_REPORT_DIR: "日报目录",
  AD_HOC_DIR: "手动任务目录",
  PARSED_MOVIES_CSV: "解析历史 CSV",
  HISTORY_DB_PATH: "历史数据库路径",
  REPORTS_DB_PATH: "报告数据库路径",
  OPERATIONS_DB_PATH: "操作数据库路径",
  PIKPAK_EMAIL: "PikPak 邮箱",
  PIKPAK_PASSWORD: "PikPak 密码",
  PIKPAK_LOG_FILE: "PikPak 日志路径",
  PIKPAK_REQUEST_DELAY: "PikPak 请求间隔（秒）",
  QB_FILE_FILTER_MIN_SIZE_MB: "最小文件大小（MB）",
  QB_FILE_FILTER_LOG_FILE: "文件过滤日志路径",
  RCLONE_CONFIG_BASE64: "Rclone 配置（Base64）",
  RCLONE_DRIVE_NAME: "Rclone 盘符名",
  RCLONE_ROOT_FOLDER: "网盘根目录",
  STORAGE_MODE: "存储模式",
  RCLONE_INVENTORY_CSV: "库存清单 CSV",
  DEDUP_CSV: "去重 CSV",
  DEDUP_DIR: "去重目录",
  DEDUP_LOG_FILE: "去重日志路径",
};

export const FIELD_DESCRIPTIONS: Record<string, string> = {
  PROXY_POOL: "多条代理按优先级排序；仅在下方的可视化编辑器中修改后才会提交保存。从服务器加载的地址若已脱敏，保存前请补全认证信息。",
  GIT_REPO_URL: "远程 Git 仓库 URL，用于 Actions / 本地同步。",
  TORRENT_SAVE_PATH: "qBittorrent 下载保存目录（与 qB 内设置一致）。",
  BASE_URL: "站点根地址，勿带末尾斜杠以外的多余路径。",
  STORAGE_MODE: "历史与报告存储方式：duo / db / csv。",
};

export function sectionTabLabel(section: string): string {
  return SECTION_TAB_LABELS[section] ?? section.replace(/\s+CONFIGURATION$/i, "").replace(/\s+MODE$/i, "").trim();
}

export function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? humanizeKey(key);
}

export function fieldDescription(key: string, meta: { readonly: boolean; sensitive: boolean; type: string }): string {
  const custom = FIELD_DESCRIPTIONS[key];
  if (custom) return custom;
  if (meta.readonly) return "由配置生成器固定，仅展示。";
  if (meta.sensitive) return "敏感信息；留空表示不修改服务器上已保存的值。";
  if (meta.type === "json") return "JSON 格式；保存后将写回环境并重新生成 config.py。";
  if (meta.type === "bool") return "开启或关闭该项行为。";
  if (meta.type === "int" || meta.type === "float") return "数值；留空可能表示不提交该项（视后端校验而定）。";
  return "保存后由后端校验并写入，与 GitHub Actions / 本地生成器字段一致。";
}

/** Path-like keys: text input + folder affordance (paths are entered manually). */
const PATH_LIKE_KEYS = new Set([
  "GIT_REPO_URL",
  "TORRENT_SAVE_PATH",
  "REPORTS_DIR",
  "DAILY_REPORT_DIR",
  "AD_HOC_DIR",
  "PARSED_MOVIES_CSV",
  "HISTORY_DB_PATH",
  "REPORTS_DB_PATH",
  "OPERATIONS_DB_PATH",
  "SPIDER_LOG_FILE",
  "UPLOADER_LOG_FILE",
  "PIPELINE_LOG_FILE",
  "EMAIL_NOTIFICATION_LOG_FILE",
  "PIKPAK_LOG_FILE",
  "QB_FILE_FILTER_LOG_FILE",
  "RCLONE_ROOT_FOLDER",
  "RCLONE_INVENTORY_CSV",
  "DEDUP_CSV",
  "DEDUP_DIR",
  "DEDUP_LOG_FILE",
]);

export function isPathLikeField(key: string, type: string, sensitive: boolean): boolean {
  return type === "string" && !sensitive && PATH_LIKE_KEYS.has(key);
}

export const SELECT_OPTIONS: Partial<Record<string, { value: string; label: string }[]>> = {
  PROXY_MODE: [
    { value: "pool", label: "pool（代理池）" },
    { value: "single", label: "single（单代理）" },
  ],
  STORAGE_MODE: [
    { value: "duo", label: "duo（CSV + DB）" },
    { value: "db", label: "db" },
    { value: "csv", label: "csv" },
  ],
  LOG_LEVEL: [
    { value: "DEBUG", label: "DEBUG" },
    { value: "INFO", label: "INFO" },
    { value: "WARNING", label: "WARNING" },
    { value: "ERROR", label: "ERROR" },
  ],
};
