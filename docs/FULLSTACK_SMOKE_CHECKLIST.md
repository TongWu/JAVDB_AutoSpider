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
