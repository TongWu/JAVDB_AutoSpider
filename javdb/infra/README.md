# infra

Cross-cutting infrastructure: config, logging, masking, HTTP request handler, CSV writer, git helper, path conventions, fetch/health utilities.

## Files

| File | Purpose |
|---|---|
| `config.py` | Centralised config accessor (`cfg`) with per-variable fallback; replaces scattered `try/except ImportError` blocks. |
| `config_generator.py` | Generates `config.py` from `VAR_*` environment variables for GitHub Actions / local bootstrap. |
| `csv_writer.py` | CSV writing with merge-on-write semantics (key by `video_code`); Rust-accelerated with Python fallback; SQLite report mirroring. |
| `fetch_page.py` | Standalone CLI to fetch a URL via the spider's proxy pool + CF bypass and save HTML to a file. |
| `git_helper.py` | Git commit/push helper with pipeline (config credentials) and standalone (`github-actions[bot]`) modes. |
| `health_check.py` | Pre-flight health checks for the pipeline (proxy availability, service reachability). |
| `logging.py` | Centralised logging configuration: console/GitHub Actions formatter, verbose file format, three console styles via `LOG_STYLE`. |
| `masking.py` | Sensitive-data masking (passwords, keys, cookies, emails, IPs) for logs; Rust-accelerated with Python fallback. |
| `paths.py` | Dated subdirectory path helpers (`reports/DailyReport/YYYY/MM/`, `reports/AdHoc/YYYY/MM/`, etc.). |
| `request.py` | HTTP request handler: direct/proxy requests, CF bypass, age verification, curl_cffi TLS-fingerprint mode, retry strategies. |

## Subdirectories

(none)

## Depends on

- Upstream callers: nearly every package — `javdb.spider`, `javdb.pipeline`, `javdb.storage`, `javdb.proxy`, `javdb.integrations`, `javdb.migrations`, `apps.cli`, `apps.api`.
- Downstream: `javdb.rust_core` (CSV/masking), `config` module (root-level), standard library / curl_cffi / requests.
