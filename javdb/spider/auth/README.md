# auth

JavDB login session management: authenticates against javdb.com and refreshes the session cookie consumed by spider/api flows.

## Files

| File | Purpose |
|---|---|
| `login.py` | Auto-login flow using `RequestHandler` (curl_cffi) with CloudFlare Turnstile bypass; updates `JAVDB_SESSION_COOKIE` in `config.py`. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.login`, `javdb.spider.fetch.login_coordinator`, tests under `tests/unit/test_login.py`.
- Downstream: `javdb.infra.request`, `javdb.infra.config`, `javdb.infra.logging`.
