# Web UI Deployment

## Local Development

- Copy the environment template at the project root: `cp .env.example .env`. The same file contains **Web API** variables (`API_SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`) and **Docker/cron** sections. `apps/api/server.py` auto-loads the root `.env` on startup (does not overwrite variables already exported in the shell).
- Backend API: `uvicorn apps.api.server:app --reload --port 8100`
- Frontend directory: `apps/web/`
- Frontend env variable: `VITE_API_BASE=http://localhost:8100`

## Docker Fullstack

- Compose file: `docker/docker-compose.fullstack.yml`
- Start: `docker compose -f docker/docker-compose.fullstack.yml up -d --build`
- Access:
  - Web: `http://localhost:8088`
  - API: `http://localhost:8100`

## Security Variables

| Variable | Purpose |
|----------|---------|
| `API_SECRET_KEY` | JWT signing key (recommend 32+ random characters) |
| `SECRETS_ENCRYPTION_KEY` | Sensitive config encryption key (Fernet key) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Initial admin credentials |

## UI Theme

Colors and typography align with the in-repo **`frontend_mdc_ng`** reference (Next + Tailwind: zinc neutrals, indigo accent, Inter font). Token definitions are in the top comments of `apps/web/src/styles.css`.

## Responsive Layout

The frontend supports three breakpoints:

| Breakpoint | Width | Behavior |
|-----------|-------|----------|
| Desktop | > 1024px | Full sidebar navigation |
| Tablet | ≤ 1024px | Navigation collapses to horizontal top bar |
| Phone | ≤ 640px | Single-column forms, full-width buttons |
