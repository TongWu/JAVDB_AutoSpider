Verify each finding against the current code and only fix it if needed.



JAVDB_AutoSpider/packages/python/javdb_spider/fetch/fallback.py Catch proxy-ban errors in initial detail fetch path: try_fetch_and_parse now re-raises ProxyBannedError, but the phase-0 call here is not wrapped, so a ban/HTTP-403 on the first proxy can escape fetch_detail_page_with_fallback and crash sequential detail processing instead of continuing to login/proxy fallback. This regression is specific to the new ban-exception flow and can stop the whole run on a single banned proxy response.



JAVDB_AutoSpider/packages/python/javdb_spider/fetch/fallback.py Do not pre-switch proxies after ban-triggered auto-switch: This loop always calls mark_failure_and_switch() before trying a proxy, but when the previous attempt returned banned=True, RequestHandler has already called ban_proxy() and advanced to the next proxy. The next iteration then marks that untried proxy as failed and skips past it, which can bypass healthy proxies and prematurely exhaust the pool.



In `@docs/FRONTEND_AI_TASK_SPEC.md` at line 313, The spec currently mixes the new
QB_URL config with old QB_HOST/QB_PORT references; update the document so all
front-end and API examples use QB_URL (not QB_HOST or QB_PORT): replace
references in section 2.2 and the /api/config example to expose/read/write
QB_URL, clarify expected QB_URL format (including scheme and optional port), and
add a brief migration note that the UI/backend now use QB_URL and existing
QB_HOST/QB_PORT are deprecated; keep MOVIE_SLEEP and MovieSleepManager docs
as-is but ensure no other references to QB_HOST/QB_PORT remain.



In `@packages/python/javdb_integrations/health_check.py` around lines 238 - 240,
The current proxy gate sets should_check_proxy using resolve_proxy_override and
bool(PROXY_MODULES), which can enable proxy checks for modules that actually run
direct; update the logic to reuse the same policy used by the spider by calling
should_proxy_module('spider', args, PROXY_MODULES, PROXY_MODE) (or the existing
should_proxy_module helper) instead of checking bool(PROXY_MODULES);
specifically, replace the rhs of should_check_proxy so it mirrors run_service.py
policy (use should_proxy_module for 'spider' with the same args/overrides) while
keeping resolve_proxy_override(args.use_proxy, args.no_proxy) to respect
explicit overrides.



In `@packages/python/javdb_integrations/health_check.py` around lines 263 - 277,
The health-check currently treats check_proxy_pool_status() (which reads the
process-local ban manager) as a critical gate and can falsely report proxies
healthy; change this by either (A) replacing the ban-manager check with an
active probe routine that attempts a test request through the configured proxies
(implement a new function like check_proxy_pool_probe() and call it instead of
check_proxy_pool_status(), returning (success,msg) and appending ("Proxy Pool",
success, msg) and setting all_passed only based on the probe result), or (B) if
you prefer not to probe, stop treating ban-manager state as critical: keep using
check_proxy_pool_status() only for informational logging (always append ("Proxy
Pool", True/Skipped,msg) but do not set all_passed = False based on it) and
change the logger branches around check_proxy_pool_status() so proxy failures
only emit warnings rather than failing the overall health check when PROXY_MODE
!= 'pool'. Ensure you modify the branch that references PROXY_MODE, the
results.append call, and the logger.info/logger.error paths to reflect the new
behavior.



In `@packages/python/javdb_migrations/tools/align_inventory_with_moviehistory.py`
around lines 271 - 279, The helper _read_csv_rows currently swallows all
exceptions and returns [] which causes corrupted/unreadable CSVs to be treated
as empty and silently dropped; change it to only return [] on FileNotFoundError,
but on other exceptions log the error via logger.warning and re-raise (or raise
a descriptive exception) instead of returning an empty list so the caller can
detect and handle read/parse failures; keep the same function name
_read_csv_rows and the logger.warning call but follow it with raising the caught
exception (or a new one) rather than returning [].



In `@packages/python/javdb_platform/db.py` around lines 1523 - 1536, The function
db_upsert_align_no_exact_match currently inserts video_code.strip().upper()
which allows whitespace-only inputs to become an empty key; normalize the code
first (e.g., normalized = video_code.strip().upper()) and if normalized is
empty, bail out (return early) instead of executing the INSERT so
blank/malformed codes are not written to InventoryAlignNoExactMatch; update
db_upsert_align_no_exact_match to perform this guard before opening the DB/doing
the conn.execute.



In `@packages/python/javdb_platform/logging_config.py` around lines 96 - 100, The
format method mutates LogRecord.name and currently restores it only after
calling super().format(record), which can leak a shortened name if super()
raises; wrap the call to super().format(record) in a try/finally inside the
Formatter.format implementation (keeping saved = record.name and record.name =
_shorten_logger_name(record.name) before), and restore record.name = saved in
the finally block so the original name is always restored even on exceptions.



In `@packages/python/javdb_platform/qb_config.py` around lines 52 - 71, The
function _normalize_qb_url currently accepts http:// silently; change it to
reject plain-http unless the allow_insecure_http flag is truthy: if
parsed.scheme == "http" and not allow_insecure_http, raise ValueError (or
require explicit opt-in) so we don't silently downgrade; use the
allow_insecure_http parameter when deciding allowed schemes. Also update
qb_base_url_candidates to stop appending an "http://" fallback for every HTTPS
endpoint unless allow_insecure_http is set—only add HTTP fallback when
allow_insecure_http is truthy. Reference the symbols _normalize_qb_url and
qb_base_url_candidates and ensure both honor the allow_insecure_http flag.



In `@packages/python/javdb_platform/request_handler.py` around lines 874 - 881,
The code is raising ProxyBannedError when the proxy pool has no usable proxies,
which causes callers (e.g., the proxy_pool.ban_proxy handler) to treat a
temporary exhaustion as a ban; change the raised exception to a new/appropriate
type like ProxyExhaustedError or ProxyUnavailableError (create that exception
class if it doesn't exist) in the RequestHandler branch that checks
proxy_pool.get_current_proxy_name() (and do the same change in the similar
branch around lines 924-928), and update any catch sites that currently catch
ProxyBannedError to only ban proxies on genuine ban errors while treating the
new ProxyExhaustedError as a non-banning signal to back off/retry.



In `@packages/python/javdb_spider/fetch/fallback.py` around lines 416 - 418, The
catch for ProxyBannedError is causing a double switch because
RequestHandler.get_page() already calls proxy_pool.ban_proxy(...) which advances
the pool; when returning from fetch/detail code you must not trigger
mark_failure_and_switch again. Update the ProxyBannedError handlers (in
fallback.py around the block that returns "..., False, False, True" and the
similar block at ~487-500) to log the ban but return values that indicate
banned=True while NOT setting the "mark failure and switch" flag or incrementing
failure counts (i.e., keep failure/mark-switch flags false and only set
banned=True), so the pool is only advanced once by proxy_pool.ban_proxy().



In `@packages/python/javdb_spider/fetch/fetch_engine.py` around lines 442 - 445,
The current branch treats any entry in task.failed_proxies as counting toward
"all proxies failed" even though _handle_proxy_banned() removes/bans proxies and
_active_workers excludes them; update the check in the block that uses
self.proxy_name, task.failed_proxies and self._active_workers (before calling
self.result_queue.put(EngineResult(...))) to ignore banned proxies — e.g.,
compute the number of failed_non_banned by filtering task.failed_proxies against
the set of banned proxies (or otherwise exclude proxies tracked by your banned
list), then compare len(failed_non_banned) to self._active_workers so banned
workers are not counted when deciding to fail the task.



In `@packages/python/javdb_spider/fetch/fetch_engine.py` around lines 519 - 559,
The race occurs in _handle_proxy_banned where the check of _active_workers and
requeue_front can conflict with another worker that wins the drain path; fix by
making the decision and state transition atomic under the same _drain_lock used
for _drain_done: acquire _drain_lock before checking/reading _active_workers and
before calling requeue_front or starting the drain so only one worker can choose
to requeue vs drain; move the requeue_front(...) call and the branch that sets
_drain_done and calls _drain_remaining_tasks() inside that locked section;
update _drain_remaining_tasks to also drain login_queue (in addition to
task_queue) and emit EngineResult entries to result_queue for items from both
queues so no login-routed tasks remain orphaned, and ensure tasks already marked
in task.failed_proxies are preserved when enqueuing failure results.



In `@packages/python/javdb_spider/fetch/index_parallel.py` around lines 198 - 203,
The current loop treats any fetch failure the same as a validated empty page by
incrementing consecutive_empty when result.success is False; change the logic in
the block referencing result, logger, consecutive_empty, max_consecutive_empty
and page_num so that only when result.success is True and result.data is empty
do you increment consecutive_empty and check against max_consecutive_empty — if
result.success is False, log the failure (using logger and page_num) and skip
incrementing consecutive_empty (optionally backoff/retry or continue). Apply the
same change to the analogous block around lines 283-303 that uses the same
result/consecutive_empty pattern.



In `@packages/python/javdb_spider/fetch/index_parallel.py` around lines 114 - 119,
The backend lifetime must be guarded with try/finally: after calling
build_parallel_index_backend(...) and backend.start(), wrap the subsequent work
(calls to backend.results(), CSV-name resolution, parse_index(), etc.) in a try
block and call backend.shutdown() and backend.export_login_state() in the
finally block so worker threads are always stopped and login state saved even on
exceptions; apply the same try/finally pattern to the other place where
backend.start() is invoked (the second start sequence around the existing
backend.start() usage).



In `@README_CN.md` around lines 823 - 825, The wording is inconsistent: the bullet
"会话级状态" says proxy bans are session-scoped/in-memory while the next bullet
reintroduces "8 天冷却期"; choose one model and make the three bullets consistent by
either removing the "8 天冷却期" reference or clarifying it as a separate,
longer-lived ban type, then update the phrases "会话级状态", "8 天冷却期", and "退出代码 2"
so they all reflect the same lifetime semantics (e.g., "会话级状态：禁用仅存在于当前进程内存（不写入
reports/proxy_bans.csv 或 SQLite），无冷却期" or "持久禁用：默认 8 天冷却期，记录至
reports/proxy_bans.csv"); ensure the final sentence still notes exit code 2
behavior tied to detection of a ban.