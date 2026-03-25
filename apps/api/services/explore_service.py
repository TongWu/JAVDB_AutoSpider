"""Explore and one-click API services."""

from __future__ import annotations

import asyncio
import csv
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests
import urllib3
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from apps.api.infra.security import _resolve_public_target_or_422
from apps.api.parsers import detect_page_type, parse_detail_page, parse_index_page
from apps.api.services import config_service, context
from packages.python.javdb_platform.bridges.rust_adapters.parser_adapter import (
    result_to_dict,
)
from packages.python.javdb_platform.proxy_pool import create_proxy_pool_from_config
from packages.python.javdb_platform.request_handler import (
    create_request_handler_from_config,
)

EXPLORE_DETAIL_CACHE: Dict[str, tuple[int, bool]] = {}


def _result_to_dict(result: Any) -> Dict[str, Any]:
    return result_to_dict(result)


def _runtime_proxy_pool(config_data: Dict[str, Any]):
    proxy_pool_raw = config_data.get("PROXY_POOL", [])
    if not isinstance(proxy_pool_raw, list) or not proxy_pool_raw:
        return None
    try:
        return create_proxy_pool_from_config(
            proxy_pool_raw,
            cooldown_seconds=int(
                config_data.get("PROXY_POOL_COOLDOWN_SECONDS", 691200) or 691200
            ),
            max_failures=int(
                config_data.get("PROXY_POOL_MAX_FAILURES", 3) or 3
            ),
            ban_log_file=str(context.REPO_ROOT / "reports" / "proxy_bans.csv"),
        )
    except Exception:
        return None


def _new_request_handler(config_data: Dict[str, Any]):
    proxy_pool = _runtime_proxy_pool(config_data)
    return create_request_handler_from_config(
        proxy_pool=proxy_pool,
        base_url=str(config_data.get("BASE_URL", "https://javdb.com")),
        cf_bypass_service_port=int(
            config_data.get("CF_BYPASS_SERVICE_PORT", 8000) or 8000
        ),
        cf_bypass_port_map=config_data.get("CF_BYPASS_PORT_MAP", {}) or {},
        cf_bypass_enabled=bool(config_data.get("CF_BYPASS_ENABLED", True)),
        cf_turnstile_cooldown=int(
            config_data.get("CF_TURNSTILE_COOLDOWN", 30) or 30
        ),
        fallback_cooldown=int(config_data.get("FALLBACK_COOLDOWN", 30) or 30),
        javdb_session_cookie=str(
            config_data.get("JAVDB_SESSION_COOKIE", "") or ""
        ),
        proxy_http=config_data.get("PROXY_HTTP"),
        proxy_https=config_data.get("PROXY_HTTPS"),
        proxy_modules=config_data.get("PROXY_MODULES", ["spider"]) or ["spider"],
        proxy_mode=str(config_data.get("PROXY_MODE", "pool")),
    )


def _simple_fetch_javdb_html(
    cfg: Dict[str, Any],
    url: str,
    use_cookie: bool = True,
) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://javdb.com/",
    }
    cookie = str(cfg.get("JAVDB_SESSION_COOKIE", "") or "").strip()
    if use_cookie and cookie:
        headers["Cookie"] = f"_jdb_session={cookie}"

    current_url = url
    timeout = urllib3.Timeout(connect=10, read=20)
    max_redirects = 3
    for _ in range(max_redirects + 1):
        parsed, hostname, resolved_ip = _resolve_public_target_or_422(current_url)
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        path_query = parsed.path or "/"
        if parsed.query:
            path_query += f"?{parsed.query}"
        default_port = 443 if scheme == "https" else 80
        headers["Host"] = hostname if port == default_port else f"{hostname}:{port}"
        if scheme == "https":
            pool: Any = urllib3.HTTPSConnectionPool(
                host=resolved_ip,
                port=port,
                assert_hostname=hostname,
                server_hostname=hostname,
                cert_reqs="CERT_REQUIRED",
            )
        else:
            pool = urllib3.HTTPConnectionPool(host=resolved_ip, port=port)

        resp = pool.request(
            "GET",
            path_query,
            headers=headers,
            timeout=timeout,
            redirect=False,
            retries=False,
            preload_content=True,
        )
        if 300 <= resp.status < 400:
            location = resp.headers.get("Location", "")
            if not location:
                raise ValueError(f"redirect without location ({resp.status})")
            current_url = urljoin(current_url, location)
            continue
        if resp.status >= 400:
            raise ValueError(f"http error {resp.status}")
        html = (resp.data or b"").decode("utf-8", errors="ignore")
        break
    else:
        raise ValueError("too many redirects")

    if not html.strip():
        raise ValueError("empty html")
    return html


def _validate_javdb_url_or_422(url: str) -> None:
    _resolve_public_target_or_422(url)


def _javdb_html_looks_like_error_blob(html: str) -> bool:
    if len(html) >= 200:
        return False
    lower = html.lower()
    return "traceback (most recent call last)" in lower or "error:" in lower


def _fetch_javdb_html(
    url: str,
    use_proxy: bool = True,
    use_cookie: bool = True,
) -> str:
    _validate_javdb_url_or_422(url)
    cfg = config_service.load_runtime_config()
    errors: list[str] = []
    try:
        handler = _new_request_handler(cfg)
        html = handler.get_page(
            url=url,
            use_proxy=use_proxy,
            use_cookie=use_cookie,
            module_name="spider",
            max_retries=3,
            use_cf_bypass=False,
        )
        if html:
            if _javdb_html_looks_like_error_blob(html):
                context.logger.warning(
                    "JavDB fetch returned short error-like HTML; retrying with simple fetch"
                )
                errors.append("request_handler: error-like response")
            else:
                return html
        else:
            errors.append("request_handler returned empty")
    except Exception as exc:
        errors.append(f"request_handler: {type(exc).__name__}")

    try:
        html_simple = _simple_fetch_javdb_html(cfg, url, use_cookie=use_cookie)
        if _javdb_html_looks_like_error_blob(html_simple):
            context.logger.warning(
                "simple JavDB fetch returned short error-like HTML; treating as failure"
            )
            errors.append("simple_fetch: error-like response")
        else:
            return html_simple
    except Exception as exc:
        errors.append(f"simple_fetch: {type(exc).__name__}")

    context.logger.warning("Failed to fetch JavDB HTML: %s", "; ".join(errors))
    raise HTTPException(status_code=502, detail="Failed to fetch target page")


def _pick_best_magnet(magnets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not magnets:
        return None
    preferred_tokens = ("中字", "字幕", "破解", "uncensored", "無碼", "无码")

    def _score(item: dict[str, Any]) -> tuple[int, int, int]:
        tags = [str(x) for x in item.get("tags", [])]
        name = str(item.get("name", ""))
        score = 0
        for token in preferred_tokens:
            if token in name or any(token in tag for tag in tags):
                score += 3
        if "高清" in name or "1080" in name:
            score += 2
        size_hint = str(item.get("size", "")).upper()
        if "GB" in size_hint:
            score += 1
        return (score, int(item.get("file_count", 0) or 0), len(name))

    return sorted(magnets, key=_score, reverse=True)[0]


def _qb_login_session(cfg: Dict[str, Any]) -> requests.Session:
    host = str(cfg.get("QB_HOST", "")).strip()
    port = str(cfg.get("QB_PORT", "")).strip()
    username = str(cfg.get("QB_USERNAME", "")).strip()
    password = str(cfg.get("QB_PASSWORD", "")).strip()
    if not host or not port or not username or not password:
        raise HTTPException(status_code=422, detail="qBittorrent config is incomplete")
    base_url = f"http://{host}:{port}"
    session = requests.Session()
    resp = session.post(
        f"{base_url}/api/v2/auth/login",
        data={"username": username, "password": password},
        timeout=int(cfg.get("REQUEST_TIMEOUT", 30) or 30),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to login qBittorrent")
    return session


def _qb_add_magnet(
    cfg: Dict[str, Any],
    magnet: str,
    title: str,
    category: Optional[str] = None,
) -> None:
    host = str(cfg.get("QB_HOST", "")).strip()
    port = str(cfg.get("QB_PORT", "")).strip()
    base_url = f"http://{host}:{port}"
    session = _qb_login_session(cfg)
    effective_category = category or str(
        cfg.get("TORRENT_CATEGORY_ADHOC", "") or cfg.get("TORRENT_CATEGORY", "")
    )
    payload = {
        "urls": magnet,
        "name": title,
        "category": effective_category,
        "autoTMM": "true",
        "savepath": str(cfg.get("TORRENT_SAVE_PATH", "") or ""),
        "skip_checking": str(bool(cfg.get("SKIP_CHECKING", False))).lower(),
        "addPaused": str(not bool(cfg.get("AUTO_START", True))).lower(),
    }
    resp = session.post(
        f"{base_url}/api/v2/torrents/add",
        data=payload,
        timeout=int(cfg.get("REQUEST_TIMEOUT", 30) or 30),
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to add magnet to qBittorrent")


def _resolved_history_csv_path(cfg: Dict[str, Any]) -> Path:
    reports_dir_raw = str(cfg.get("REPORTS_DIR", "reports") or "reports").strip()
    history_raw = str(
        cfg.get("PARSED_MOVIES_CSV", "parsed_movies_history.csv")
        or "parsed_movies_history.csv"
    ).strip()

    def _resolve_under_root(raw: str, field: str) -> Path:
        path = Path(raw)
        if path.is_absolute():
            raise HTTPException(status_code=422, detail=f"{field} must be a relative path")
        candidate = (context.REPO_ROOT / path).resolve()
        try:
            candidate.relative_to(context.REPO_ROOT.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{field} escapes project root",
            ) from exc
        return candidate

    def _safe_single_segment(name: str, field: str) -> str:
        if not name:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty")
        if "/" in name or "\\" in name:
            raise HTTPException(status_code=422, detail=f"{field} must be a file name")
        segment = Path(name)
        if segment.is_absolute() or len(segment.parts) != 1 or name in {".", ".."}:
            raise HTTPException(status_code=422, detail=f"{field} is invalid")
        if ".." in name:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot contain traversal markers",
            )
        return name

    reports_dir = _resolve_under_root(reports_dir_raw or "reports", "REPORTS_DIR")
    if "/" in history_raw or "\\" in history_raw:
        return _resolve_under_root(history_raw, "PARSED_MOVIES_CSV")
    history_name = _safe_single_segment(
        history_raw or "parsed_movies_history.csv",
        "PARSED_MOVIES_CSV",
    )
    candidate = (reports_dir / history_name).resolve()
    try:
        candidate.relative_to(context.REPO_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="PARSED_MOVIES_CSV escapes project root",
        ) from exc
    return candidate


def _downloaded_map_by_href(cfg: Dict[str, Any]) -> Dict[str, bool]:
    history_path = _resolved_history_csv_path(cfg)
    downloaded: Dict[str, bool] = {}
    if not history_path.exists():
        return downloaded
    try:
        with open(history_path, "r", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                href = str(row.get("href", "")).strip()
                if not href:
                    continue
                downloaded[href] = True
    except Exception:
        return {}
    return downloaded


def _inject_explore_enhancer(html: str, source_url: str, *, nonce: str = "") -> str:
    raw = str(source_url)
    safe_parts: list[str] = []
    for ch in raw:
        if ch in "<>":
            continue
        codepoint = ord(ch)
        if codepoint < 32 or codepoint == 127:
            continue
        safe_parts.append(ch)
    escaped_url = json.dumps("".join(safe_parts))
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    enhancer = f"""
<script{nonce_attr}>
(function() {{
  const SOURCE_URL = {escaped_url};
  const authHeaders = {{ "Content-Type": "application/json" }};

  function abs(href) {{
    try {{ return new URL(href, SOURCE_URL).toString(); }} catch (e) {{ return href; }}
  }}
  function isDetail(url) {{
    try {{ return /^\\/v\\//.test(new URL(url).pathname); }} catch (e) {{ return false; }}
  }}
  function isJavdb(url) {{
    try {{ return /(^|\\.)javdb\\.com$/i.test(new URL(url).hostname); }} catch (e) {{ return false; }}
  }}
  function postToParent(type, payload) {{
    window.parent && window.parent.postMessage(Object.assign({{ type }}, payload || {{}}), "*");
  }}
  function notifyUrl() {{
    postToParent("explore:url", {{ url: SOURCE_URL }});
  }}
  function linkToProxy(url) {{
    const u = new URL("/api/explore/proxy-page", window.location.origin);
    u.searchParams.set("url", url);
    return u.toString();
  }}
  function patchLinks() {{
    document.querySelectorAll("a[href]").forEach((a) => {{
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      const target = abs(href);
      if (!isJavdb(target)) return;
      a.setAttribute("href", linkToProxy(target));
    }});
  }}
  async function apiPost(path, payload) {{
    const res = await fetch(path, {{
      method: "POST",
      headers: authHeaders,
      credentials: "include",
      body: JSON.stringify(payload || {{}})
    }});
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json().catch(() => ({{}}));
  }}
  function mkBtn(text) {{
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.style.marginLeft = "8px";
    b.style.padding = "3px 8px";
    b.style.fontSize = "12px";
    b.style.border = "1px solid #ccc";
    b.style.borderRadius = "4px";
    b.style.cursor = "pointer";
    b.style.background = "#fff";
    return b;
  }}
  function addAdhocJump() {{
    if (!isJavdb(SOURCE_URL) || isDetail(SOURCE_URL)) return;
    const bar = document.querySelector(".tabs") || document.body;
    const btn = mkBtn("跳转至adhoc任务创建");
    btn.addEventListener("click", () => postToParent("explore:jump-adhoc", {{ url: SOURCE_URL }}));
    bar.appendChild(btn);
  }}
  function addDetailButtons() {{
    if (!isDetail(SOURCE_URL)) return;
    const rows = document.querySelectorAll("#magnets-content .item.columns.is-desktop");
    rows.forEach((row) => {{
      const anchor = row.querySelector(".magnet-name a[href^='magnet:']");
      if (!anchor || row.querySelector(".explore-qb-btn")) return;
      const btn = mkBtn("使用qBittorrent下载");
      btn.className = "explore-qb-btn";
      btn.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/download-magnet", {{
            magnet: anchor.getAttribute("href") || "",
            title: (anchor.querySelector(".name") && anchor.querySelector(".name").textContent || "").trim()
          }});
          btn.textContent = "已提交";
        }} catch (e) {{
          btn.textContent = "失败";
        }}
      }});
      anchor.parentElement && anchor.parentElement.appendChild(btn);
    }});
    const host = document.querySelector("#magnets-content");
    if (host && !document.querySelector(".explore-one-click-btn")) {{
      const one = mkBtn("一键下载");
      one.className = "explore-one-click-btn";
      one.style.margin = "8px 0";
      one.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: SOURCE_URL, use_proxy: false, use_cookie: true }});
          one.textContent = "已提交";
        }} catch (e) {{
          one.textContent = "失败";
        }}
      }});
      host.parentElement && host.parentElement.insertBefore(one, host);
    }}
  }}
  function addIndexButtons() {{
    const cards = document.querySelectorAll(".movie-list .item a.box[href]");
    if (!cards.length) return;
    cards.forEach((a) => {{
      if (a.querySelector(".explore-card-oneclick")) return;
      const btn = mkBtn("一键下载");
      btn.className = "explore-card-oneclick";
      btn.addEventListener("click", async (e) => {{
        e.preventDefault();
        e.stopPropagation();
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          btn.textContent = "已提交";
        }} catch (err) {{
          btn.textContent = "失败";
        }}
      }});
      const title = a.querySelector(".video-title");
      if (title && title.parentElement) title.parentElement.appendChild(btn);
    }});
    if (!document.querySelector(".explore-page-oneclick")) {{
      const pageBtn = mkBtn("整页一键下载");
      pageBtn.className = "explore-page-oneclick";
      pageBtn.style.margin = "8px 0";
      pageBtn.addEventListener("click", async () => {{
        for (const a of cards) {{
          try {{
            await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          }} catch (e) {{}}
        }}
        pageBtn.textContent = "已提交";
      }});
      const mount = document.querySelector(".movie-list") || document.body;
      mount.parentElement && mount.parentElement.insertBefore(pageBtn, mount);
    }}
  }}
  async function addIndexStatusTags() {{
    const cards = Array.from(document.querySelectorAll(".movie-list .item a.box[href]"));
    if (!cards.length) return;
    const movies = cards.map((a) => {{
      const href = abs(a.getAttribute("href") || "");
      const codeNode = a.querySelector(".uid");
      return {{ href, video_code: (codeNode && codeNode.textContent || "").trim() }};
    }});
    try {{
      const data = await apiPost("/api/explore/index-status", {{ movies, use_proxy: false, use_cookie: true }});
      const items = data.items || {{}};
      cards.forEach((a) => {{
        const href = abs(a.getAttribute("href") || "");
        const st = items[href];
        if (!st) return;
        if (a.querySelector(".explore-status-row")) return;
        const row = document.createElement("div");
        row.className = "explore-status-row";
        row.style.marginTop = "4px";
        row.style.fontSize = "11px";
        row.style.color = "#666";
        row.textContent = (st.has_uncensored ? "有无码" : "无码未知/无") + " · " + (st.downloaded ? "已下载" : "未下载");
        const title = a.querySelector(".video-title");
        if (title && title.parentElement) title.parentElement.appendChild(row);
      }});
    }} catch (e) {{}}
  }}
  function init() {{
    notifyUrl();
    patchLinks();
    addAdhocJump();
    addDetailButtons();
    addIndexButtons();
    addIndexStatusTags();
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", init);
  }} else {{
    init();
  }}
}})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", enhancer + "</body>")
    return html + enhancer


def _has_uncensored_magnet(
    absolute_url: str,
    use_proxy: bool,
    use_cookie: bool,
) -> bool:
    now = int(time.time())
    cached = EXPLORE_DETAIL_CACHE.get(absolute_url)
    if cached and (now - cached[0]) < context.EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS:
        return cached[1]

    has_uncensored = False
    try:
        html = _fetch_javdb_html(
            absolute_url,
            use_proxy=use_proxy,
            use_cookie=use_cookie,
        )
        detail = _result_to_dict(parse_detail_page(html))
        magnets = detail.get("magnets", [])
        if isinstance(magnets, list):
            for magnet in magnets:
                tags = (
                    [str(x) for x in magnet.get("tags", [])]
                    if isinstance(magnet, dict)
                    else []
                )
                name = str(magnet.get("name", "")) if isinstance(magnet, dict) else ""
                if any(
                    token in name for token in ("無碼", "无码", "uncensored")
                ) or any(
                    any(token in tag for token in ("無碼", "无码", "uncensored"))
                    for tag in tags
                ):
                    has_uncensored = True
                    break
    except Exception:
        has_uncensored = False

    EXPLORE_DETAIL_CACHE[absolute_url] = (now, has_uncensored)
    if len(EXPLORE_DETAIL_CACHE) > context.EXPLORE_INDEX_STATUS_CACHE_MAX_ITEMS:
        stale = [
            url
            for url, (ts, _) in EXPLORE_DETAIL_CACHE.items()
            if (now - ts) >= context.EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS
        ]
        for url in stale:
            EXPLORE_DETAIL_CACHE.pop(url, None)
    return has_uncensored


def sync_cookie_payload(cookie: str, username: str) -> Dict[str, str]:
    return config_service.set_javdb_session_cookie(cookie, username)


async def proxy_page_payload(url: str, username: str) -> HTMLResponse:
    _validate_javdb_url_or_422(url)
    try:
        html = _fetch_javdb_html(url, use_proxy=False, use_cookie=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch target page",
        ) from exc

    nonce = secrets.token_urlsafe(16)
    injected = _inject_explore_enhancer(html, url, nonce=nonce)
    context.audit_logger.info("explore_proxy_page username=%s", username)
    csp = (
        f"script-src 'nonce-{nonce}'; "
        "style-src * 'unsafe-inline'; "
        "img-src * data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return HTMLResponse(
        content=injected,
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
        },
    )


async def resolve_payload(payload: Any, username: str) -> Dict[str, Any]:
    _validate_javdb_url_or_422(payload.url)
    html = _fetch_javdb_html(
        payload.url,
        use_proxy=payload.use_proxy,
        use_cookie=payload.use_cookie,
    )
    page_type = detect_page_type(html)
    body: Dict[str, Any] = {
        "url": payload.url,
        "page_type": page_type,
    }
    if page_type == "detail":
        body["detail"] = _result_to_dict(parse_detail_page(html))
    else:
        body["index"] = _result_to_dict(parse_index_page(html, payload.page_num))
    context.audit_logger.info(
        "explore_resolve username=%s page_type=%s",
        username,
        page_type,
    )
    return body


async def download_magnet_payload(payload: Any, username: str) -> Dict[str, str]:
    cfg = config_service.load_runtime_config()
    _qb_add_magnet(cfg, payload.magnet, payload.title or "JavDB", payload.category)
    context.audit_logger.info("explore_download_magnet username=%s", username)
    return {"status": "ok"}


async def one_click_payload(payload: Any, username: str) -> Dict[str, Any]:
    _validate_javdb_url_or_422(payload.detail_url)
    html = _fetch_javdb_html(
        payload.detail_url,
        use_proxy=payload.use_proxy,
        use_cookie=payload.use_cookie,
    )
    detail = _result_to_dict(parse_detail_page(html))
    magnets = detail.get("magnets", [])
    best = _pick_best_magnet(magnets if isinstance(magnets, list) else [])
    if not best:
        raise HTTPException(status_code=404, detail="No magnet found in detail page")
    cfg = config_service.load_runtime_config()
    title = str(best.get("name") or detail.get("video_code") or "JavDB")
    _qb_add_magnet(cfg, str(best.get("href", "")), title, payload.category)
    context.audit_logger.info("explore_one_click username=%s", username)
    return {
        "status": "ok",
        "selected": best,
        "video_code": detail.get("video_code", ""),
    }


async def index_status_payload(payload: Any, username: str) -> Dict[str, Any]:
    cfg = config_service.load_runtime_config()
    downloaded_map = _downloaded_map_by_href(cfg)
    statuses: Dict[str, Dict[str, Any]] = {}
    candidates: list[tuple[str, str]] = []
    for item in payload.movies[: context.EXPLORE_INDEX_STATUS_MAX_ITEMS]:
        href = str(item.get("href", "")).strip()
        if not href:
            continue
        absolute_url = href if href.startswith("http") else f"https://javdb.com{href}"
        try:
            _validate_javdb_url_or_422(absolute_url)
        except HTTPException:
            continue
        is_downloaded = bool(downloaded_map.get(href) or downloaded_map.get(absolute_url))
        statuses[href] = {
            "downloaded": is_downloaded,
            "has_uncensored": False,
        }
        candidates.append((href, absolute_url))

    semaphore = asyncio.Semaphore(context.EXPLORE_INDEX_STATUS_CONCURRENCY)

    async def _fetch_status(target_href: str, target_url: str) -> tuple[str, bool]:
        async with semaphore:
            try:
                has_uncensored = await asyncio.wait_for(
                    asyncio.to_thread(
                        _has_uncensored_magnet,
                        target_url,
                        payload.use_proxy,
                        payload.use_cookie,
                    ),
                    timeout=context.EXPLORE_INDEX_STATUS_ITEM_TIMEOUT_SECONDS,
                )
            except Exception:
                has_uncensored = False
            return target_href, has_uncensored

    tasks = [asyncio.create_task(_fetch_status(href, url)) for href, url in candidates]
    done_count = 0
    timeout_count = 0
    if tasks:
        done, pending = await asyncio.wait(
            tasks,
            timeout=context.EXPLORE_INDEX_STATUS_TOTAL_TIMEOUT_SECONDS,
        )
        for task in done:
            try:
                href, has_uncensored = task.result()
            except Exception:
                continue
            if href in statuses:
                statuses[href]["has_uncensored"] = has_uncensored
                done_count += 1
        timeout_count = len(pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    context.audit_logger.info(
        "explore_index_status username=%s count=%s done=%s timeout=%s",
        username,
        len(statuses),
        done_count,
        timeout_count,
    )
    return {"items": statuses}


__all__ = [
    "_downloaded_map_by_href",
    "_fetch_javdb_html",
    "_has_uncensored_magnet",
    "_inject_explore_enhancer",
    "_pick_best_magnet",
    "_qb_add_magnet",
    "_qb_login_session",
    "_resolve_public_target_or_422",
    "_resolved_history_csv_path",
    "_validate_javdb_url_or_422",
    "download_magnet_payload",
    "index_status_payload",
    "one_click_payload",
    "proxy_page_payload",
    "resolve_payload",
    "sync_cookie_payload",
]
