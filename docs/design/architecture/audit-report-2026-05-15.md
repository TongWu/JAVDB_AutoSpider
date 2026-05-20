# Documentation Audit Report (2026-05-15)

This report captures all discrepancies found between code and documentation.
It serves as the authoritative source for Plan B corrections.

---

## B1: CLI Parameter Audit

### Undocumented arguments (8)

| Flag | Type | Default | Source |
|------|------|---------|--------|
| `--from-pipeline` | store_true | False | cli.py (internal flag) |
| `--no-rclone-filter` | store_true | False | cli.py |
| `--disable-all-filters` | store_true | False | cli.py |
| `--enable-dedup` | store_true | False | cli.py |
| `--enable-redownload` | store_true | False | cli.py |
| `--redownload-threshold` | float | 0.30 | cli.py |
| `--pikpak-individual` | store_true | False | pipeline_service.py |
| `--no-redownload` | store_true | False | pipeline_service.py |

### Misleading descriptions (7)

1. `--ignore-history`: README says "both daily & ad hoc" but ad-hoc already ignores history by default
2. `--output-file`: README omits "without changing directory" clarification
3. `--url`: README says "enables ad hoc mode" but code says "add ?page=x for pages"
4. `--ignore-release-date`: README truncates "and download all entries matching phase criteria"
5. `--sequential`: README says "disable parallel" but code scopes to "proxy pool mode"
6. `--use-proxy/--no-proxy`: README uses generic phrasing, code is spider/pipeline-specific
7. `--ignore-history` vs `--use-history`: Interaction not clearly documented

---

## B2: config.py.example Audit

### Undocumented variables (25)

**qBittorrent Adhoc:** QB_ALLOW_INSECURE_HTTP, QB_URL_ADHOC, QB_USERNAME_ADHOC, QB_PASSWORD_ADHOC
**Proxy Coordinator:** PROXY_COORDINATOR_URL, PROXY_COORDINATOR_TOKEN, MOVIE_CLAIM_ENABLED, RUNNER_REGISTRY_ENABLED
**Captcha (GPT):** GPT_API_URL, GPT_API_KEY
**Login Policy:** LOGIN_ATTEMPTS_PER_PROXY_LIMIT, LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH, LOGIN_VERIFICATION_URLS
**Re-download:** INCLUDE_DOWNLOADED_IN_REPORT, ENABLE_REDOWNLOAD, REDOWNLOAD_SIZE_THRESHOLD
**Database Paths:** HISTORY_DB_PATH, REPORTS_DB_PATH, OPERATIONS_DB_PATH
**PikPak:** PIKPAK_ROOT_FOLDER
**Rclone/Dedup:** RCLONE_CONFIG_BASE64, RCLONE_FOLDER_PATH, RCLONE_INVENTORY_CSV, DEDUP_CSV, DEDUP_LOG_FILE
**SMTP:** SMTP_PORT

### Documented but missing from config.py.example (1)

- `TWOCAPTCHA_API_KEY`: README documents 2Captcha but config.py.example now uses GPT-based captcha

### Value discrepancies (3)

1. **PROXY_MODE**: README says 'single', config.py.example says 'pool'. Also 'None' option undocumented.
2. **PHASE2_MIN_COMMENTS**: README says 80, config.py.example says 100
3. **Captcha system**: Entire approach changed from 2Captcha to GPT-based, README not updated

---

## B3: GitHub Actions Workflow Audit

### Entirely undocumented workflows (5)

Migration.yml, WeeklyDedup.yml, RollbackD1.yml, StaleSessionCleanup.yml, AuditArchive.yml

### Undocumented workflow_dispatch inputs: 84 total

- DailyIngestion: 11 inputs
- AdHocIngestion: 19 inputs
- RollbackD1: 10 inputs
- Migration: 21 inputs
- TestIngestion: 2 inputs
- WeeklyDedup: 8 inputs
- StaleSessionCleanup: 5 inputs
- AuditArchive: 5 inputs
- QBFileFilter: 3 inputs

### Stale/wrong documented values (6)

1. DailyIngestion cron: wiki says 10:00 UTC, actual 12:00 UTC
2. Environment name: wiki says WT_DailyIngestion, actual is Production
3. QBFileFilter cron: wiki says 12:00 UTC (2h after Daily), actual 16:00 UTC (4h after)
4. DailyIngestion permissions: wiki says contents:write, actual top-level is contents:read
5. QBFileFilter inputs: wiki shows 3, actual has 6
6. Wiki workflow list shows 15, repo has 20

### Behavioral discrepancies (12)

Key items:
- StaleSessionCleanup cron runs as dry-run only (apply defaults to false)
- AuditArchive cron also runs as dry-run only
- WeeklyDedup scheduled runs use ubuntu-latest despite self-hosted default
- QBFileFilter has 3 jobs (including adhoc), wiki documents 2

---

## B4: Storage Backend Audit

### Session lifecycle: MATCH
All four states (in_progress → finalizing → committed / failed) consistent across code and docs.

### Pending mode: MATCH
Stage/commit/delete/resume logic matches docs exactly.

### Audit mode deprecation: MATCH
DeprecationWarning emitted; kill switch implemented; audit still accepted (correctly, pre-sunset 2026-08-13).

### Rollback CLI: MATCH
Cross-day 1h window, --force override, exit codes 0/2/3/4 all match docs.

### Dates/timelines: MOSTLY CURRENT
Sunset 2026-08-13 still future; Phase 4 changes implemented.

### Stale documentation (1 issue)

**Session ID format outdated in 3 locations:**
- d1-rollback.md lines 62-68: still shows "51-bit integer snowflake" with `(time.time_ns() // 1_000_000) << 10`
- CLAUDE.md line 53: says "51-bit application-generated snowflake"
- README.md lines 99/101: references "51-bit application-generated snowflake id"
- **Actual code** (db_session.py): now generates TEXT string in format `YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`
- The rationale for application-generated IDs is still correct; only the format description is stale.
