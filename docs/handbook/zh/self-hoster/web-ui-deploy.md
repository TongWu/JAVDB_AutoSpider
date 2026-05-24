# Web UI 部署说明

Web UI 已迁移到独立仓库：[`javdb-autospider-web`](https://github.com/tedwu/javdb-autospider-web)。基于 Vue 3 + Naive UI 构建的 SPA，通过 REST API 与后端通信。

## 前提条件

- 后端 API 已运行（默认 `http://localhost:8100`）
- Node.js 20+（本地开发需要）

## 本地开发

```bash
# 克隆前端仓库
git clone https://github.com/tedwu/javdb-autospider-web.git
cd javdb-autospider-web

# 安装依赖
npm install

# 设置 API 地址
cp .env.example .env
# 编辑 .env: VITE_API_BASE=http://localhost:8100

# 启动开发服务器
npm run dev
```

后端 API（另开终端）：

```bash
# 在主仓库目录下
uvicorn apps.api.server:app --reload --port 8100
```

## Docker Compose（分离部署）

```yaml
services:
  api:
    build: .
    ports:
      - "8100:8100"
    env_file: .env

  web:
    image: ghcr.io/tedwu/javdb-autospider-web:latest
    ports:
      - "8088:80"
    depends_on:
      - api
```

已发布的 web 镜像内部 nginx 会把 `/api` 反向代理到 Docker 网络上的 `api`
服务,浏览器只会访问 `http://localhost:8088`(同源)。**不要**设置
`VITE_API_BASE=http://api:8100` —— 宿主机浏览器无法解析 Docker 内部 DNS,
`http://api:8100` 在浏览器里会 404。

## Docker Compose（一体化启动）

主仓库包含一体化编排文件：

```bash
docker compose -f docker/docker-compose.fullstack.yml up -d --build
```

- Web: `http://localhost:8088`
- API: `http://localhost:8100`

## 安全相关变量

| 变量 | 用途 |
| ---- | ---- |
| `API_SECRET_KEY` | JWT 签名密钥（建议 32+ 长度随机值） |
| `SECRETS_ENCRYPTION_KEY` | 敏感配置加密密钥（Fernet key） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 管理员初始账号 |

## 架构设计

详见 [ADR-008](../../../design/_archive/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.zh.md)（Vue 3 + Naive UI、Pinia 状态管理、i18n 国际化、E2E 测试策略）。
