# Fullstack 冒烟检查清单

## 0. 前置条件

- 已有 `config.py`（可由 `python3 utils/config_generator.py` 生成）。
- 已设置关键环境变量：
  - `API_SECRET_KEY`
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
  - （可选）`SECRETS_ENCRYPTION_KEY`

## 1. 后端 API 本地检查

- 启动：
  - `uvicorn api.server:app --host 0.0.0.0 --port 8100 --reload`
- 检查健康：
  - `curl http://localhost:8100/api/health`
  - 预期：返回 `status=ok`
- 登录拿 token：
  - `curl -X POST http://localhost:8100/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"你的密码"}'`
  - 记录 `access_token` 与 `csrf_token`
- 鉴权校验：
  - 未带 token 调 `GET /api/config` 应返回 401
  - 带 token + CSRF 调 `GET /api/config` 应返回脱敏配置
- `GET /api/config/meta` 返回字段列表（含 `section` / `type` / `sensitive` / `readonly`），供前端分组表单使用

## 2. 前端本地检查

- 已验证构建通过：
  - `cd web && npm install && npm run build`
- 开发运行：
  - `cd web && npm run dev`
- 浏览器验证：
  - 访问 `http://localhost:5173`
  - 登录后可进入 Dashboard / Config / Daily / Adhoc / Tasks
  - 配置页保存后提示成功并可重新加载

## 3. 任务链路检查（核心）

- Daily：
  - 前端提交 `Daily` 任务，拿到 `job_id`
  - 在 Tasks 页查询该 `job_id`，状态可见 `running -> success/failed`
- Adhoc：
  - 使用合法 `javdb.com` URL 提交
  - 非法 URL（如 `localhost`）应被后端拒绝（422）

## 4. 响应式样式检查（Pad / Phone）

- Desktop（>1024px）：
  - 侧边导航显示正常
- Pad（<=1024px）：
  - 导航切换为顶部横向区域
  - 页面内容不溢出
- Phone（<=640px）：
  - 表单单列布局
  - 按钮满宽可点击
  - 日志区域可滚动

## 5. Docker 一体化检查

- 启动：
  - `docker compose -f docker/docker-compose.fullstack.yml up -d --build`
- 验证：
  - `http://localhost:8088` 打开前端
  - `http://localhost:8100/api/health` 返回正常
  - 前端登录与任务触发可用

## 6. 回归与安全点

- 除 `/api/health` 与 `/api/auth/login` 外，匿名请求均不可访问。
- `GET /api/config` 敏感值必须为脱敏结果。
- 审计日志存在 `logs/audit.log`，且包含登录/配置修改/任务触发记录。

## 6.5 Electron MVP 检查

- 依赖安装：
  - 根目录执行 `npm install`
  - 前端目录执行 `cd web && npm install`
- 开发启动：
  - 根目录执行 `npm run electron:dev`
  - 预期：自动拉起 Vite、自动拉起 FastAPI（8100），并打开 Electron 窗口
- 健康检查：
  - Electron 打开后可正常登录；Dashboard / Config / Daily / Adhoc / Explore 可访问
  - 在 Electron 内触发 API 请求时应访问 `http://127.0.0.1:8100`（非浏览器跨域错误）
- 进程回收：
  - 关闭 Electron 后，不应残留由 Electron 启动的 uvicorn 子进程

## 7. R2 探索能力专项检查

- 探索入口：
  - 左侧导航出现 `探索`，点击可进入探索页。
  - 地址栏输入 `https://javdb.com` 页面后，点击“访问并解析”有返回结果（详情页或索引页）。
- Cookie 同步：
  - 在探索页粘贴 `_jdb_session` 值后点击“同步 Cookie 到配置”。
  - 再次进入配置页确认 `JAVDB_SESSION_COOKIE` 已更新（脱敏显示即可）。
- 详情页下载：
  - 详情页解析后可看到种子列表。
  - 点击“使用 qBittorrent 下载”后，qB 中应出现对应任务。
  - 点击“一键下载（最优组合）”后，qB 中应新增一条最优磁链任务。
- 索引页能力：
  - 索引页解析后可看到电影列表。
  - 单条“一键下载”可提交下载；“整页一键下载”可批量提交。
  - 点击“刷新标签”后，能看到“有无码/已下载”标签变化。
- 任务联动：
  - 探索页非详情 URL 时，“跳转至 adhoc 任务创建”可用。
  - 跳转后 `Adhoc` 页 URL 自动带入，可直接提交任务。
- 日志实时性：
  - 提交 `Daily(mode=pipeline)` 后，运行日志应持续增量更新，不再一次性刷出。
