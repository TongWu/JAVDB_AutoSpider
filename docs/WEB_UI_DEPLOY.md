# WEB UI 部署说明

## 本地开发

- 在项目根目录复制环境模板：`cp .env.example .env`。同一文件包含 **Web API** 变量（如 `API_SECRET_KEY`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`）以及 **Docker/cron** 段落；`api/server.py` 启动时会**自动加载根目录 `.env`**（不会覆盖已在 shell 里 `export` 的变量）。
- 后端 API：`uvicorn api.server:app --reload --port 8100`
- 前端目录：`web/`
- 前端环境变量：`VITE_API_BASE=http://localhost:8100`

## Docker 一体化启动

- 编排文件：`docker/docker-compose.fullstack.yml`
- 启动命令：`docker compose -f docker/docker-compose.fullstack.yml up -d --build`
- 访问地址：
  - Web: `http://localhost:8088`
  - API: `http://localhost:8100`

## 安全相关变量

- `API_SECRET_KEY`: JWT 签名密钥（建议 32+ 长度随机值）
- `SECRETS_ENCRYPTION_KEY`: 敏感配置加密密钥（Fernet key）
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`: 管理员初始账号

## 界面风格

- 颜色与排版对齐仓库内 **`frontend_mdc_ng`**（Next + Tailwind：zinc 中性色、indigo 强调、Inter 字体），令牌定义见 `web/src/styles.css` 顶部注释。

## 响应式支持

- 前端默认支持三档布局：
  - Desktop: `>1024px`
  - Tablet: `<=1024px`
  - Phone: `<=640px`
- 在平板与手机端中导航自动折叠为顶部横向区块，表单自动变为单列。
