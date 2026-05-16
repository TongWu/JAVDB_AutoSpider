# Fullstack Smoke Checklist

## 0. Prerequisites

- `config.py` exists (can be generated via `python3 utils/config_generator.py`).
- Key environment variables are set:
  - `API_SECRET_KEY`
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
  - (optional) `SECRETS_ENCRYPTION_KEY`

## 1. Backend API (Local)

- Start: `uvicorn apps.api.server:app --host 0.0.0.0 --port 8100 --reload`
- Health check: `curl http://localhost:8100/api/health` → expect `status=ok`
- Login for token:
  ```bash
  curl -X POST http://localhost:8100/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"your_password"}'
  ```
  Note the `access_token` and `csrf_token`.
- Auth verification:
  - Without token: `GET /api/config` → 401
  - With token + CSRF: `GET /api/config` → redacted config
- `GET /api/config/meta` returns field list (with `section` / `type` / `sensitive` / `readonly`) for frontend form grouping.

## 2. Frontend (Local)

- Build: `cd apps/web && npm install && npm run build`
- Dev server: `cd apps/web && npm run dev`
- Browser:
  - Visit `http://localhost:5173`
  - After login, verify Dashboard / Config / Daily / Adhoc / Tasks are accessible
  - Config page saves successfully and reloads

## 3. Task Pipeline (Core)

- **Daily**: Submit a Daily task from the frontend → get `job_id` → check Tasks page → status transitions `running → success/failed`
- **Adhoc**: Submit with a valid `javdb.com` URL; invalid URLs (e.g., `localhost`) should be rejected (422)

## 4. Responsive Layout (Tablet / Phone)

| Breakpoint | Checks |
|-----------|--------|
| Desktop (> 1024px) | Sidebar navigation visible |
| Tablet (≤ 1024px) | Navigation switches to horizontal top bar; no content overflow |
| Phone (≤ 640px) | Single-column forms; full-width buttons; scrollable log area |

## 5. Docker Fullstack

- Start: `docker compose -f docker/docker-compose.fullstack.yml up -d --build`
- Verify:
  - `http://localhost:8088` — frontend loads
  - `http://localhost:8100/api/health` — returns ok
  - Login and task submission work

## 6. Security Baseline

- Anonymous requests to any endpoint except `/api/health` and `/api/auth/login` are rejected.
- `GET /api/config` returns redacted values for sensitive fields.
- Audit log at `logs/audit.log` records login, config changes, and task triggers.

## 6.5. Electron MVP

- Install deps: `npm install` (root) + `cd apps/web && npm install`
- Start: `npm run electron:dev` from root
  - Expect: Vite dev server, FastAPI (8100), and Electron window all launch
- Verify: Login works; Dashboard / Config / Daily / Adhoc / Explore accessible inside Electron
- Process cleanup: After closing Electron, no orphaned uvicorn processes remain

## 7. Explore Feature

- Navigate to Explore from sidebar
- Enter a `javdb.com` URL → "Visit & Parse" returns results (detail or index page)
- Cookie sync: Paste `_jdb_session` → "Sync Cookie to Config" → verify in Config page
- Detail page: Torrent list visible; "Download via qBittorrent" and "One-Click Best Combo" both submit to qB
- Index page: Movie list visible; single and batch download work; "Refresh Tags" updates markers
- Task link: Non-detail URLs show "Jump to Adhoc" → Adhoc page auto-fills the URL
- Live logs: After submitting a Daily (pipeline mode) task, logs stream incrementally
