# Web UI Deployment

The Web UI lives in a standalone repository: [`javdb-autospider-web`](https://github.com/tedwu/javdb-autospider-web). It is a Vue 3 + Naive UI SPA that talks to the backend API.

## Prerequisites

- Backend API running at a known URL (default `http://localhost:8100`)
- Node.js 20+ (for local development)

## Local Development

```bash
# Clone the frontend repo
git clone https://github.com/tedwu/javdb-autospider-web.git
cd javdb-autospider-web

# Install dependencies
npm install

# Set API base URL
cp .env.example .env
# Edit .env: VITE_API_BASE=http://localhost:8100

# Start dev server
npm run dev
```

Backend API (separate terminal):

```bash
# In the main repo
uvicorn apps.api.server:app --reload --port 8100
```

## Docker Compose (Split Deploy)

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

The published web image's nginx proxies `/api` to the `api` service on the
Docker network, so the browser only talks to `http://localhost:8088`
(same-origin). Do **not** set `VITE_API_BASE=http://api:8100` — Docker's
internal DNS isn't reachable from a host-side browser, and `http://api:8100`
would 404 in-browser.

## Docker Compose (Fullstack)

The main repo includes a fullstack compose file:

```bash
docker compose -f docker/docker-compose.fullstack.yml up -d --build
```

- Web: `http://localhost:8088`
- API: `http://localhost:8100`

## Security Variables

| Variable | Purpose |
|----------|---------|
| `API_SECRET_KEY` | JWT signing key (recommend 32+ random characters) |
| `SECRETS_ENCRYPTION_KEY` | Sensitive config encryption key (Fernet key) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Initial admin credentials |

## Architecture

See [ADR-008](../../../design/adr/ADR-008-frontend-rewrite-architecture.md) for the full design rationale (Vue 3 + Naive UI, Pinia stores, i18n, E2E test strategy).
